import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { parseArgs, runCli } from "../../lib/cli.mjs";
import {
  compareVersions,
  copyApplication,
  inspectInstallation,
  installManaged,
  migrationPlan,
  rollbackManaged,
  uninstallManaged,
  waitForHealth,
} from "../../lib/installer.mjs";
import { assertSafeManagedHome, isWithin, resolveLayout } from "../../lib/layout.mjs";

test("parseArgs accepts managed install options", () => {
  assert.deepEqual(parseArgs(["install", "--home", "/tmp/control", "--no-start"]), {
    command: "install",
    home: "/tmp/control",
    source: null,
    to: null,
    port: null,
    start: false,
    purge: false,
    yes: false,
    plan: false,
  });
});

test("parseArgs accepts top-level help", () => {
  assert.equal(parseArgs(["--help"]).command, "help");
  assert.equal(parseArgs(["-h"]).command, "help");
});

test("parseArgs accepts explicit rollback target", () => {
  const parsed = parseArgs(["rollback", "--to", "1.4.3", "--yes"]);
  assert.equal(parsed.command, "rollback");
  assert.equal(parsed.to, "1.4.3");
  assert.equal(parsed.yes, true);
});

test("parseArgs accepts an install port", () => {
  assert.equal(parseArgs(["install", "--port", "19000"]).port, 19000);
  assert.throws(() => parseArgs(["install", "--port", "nope"]), /정수/);
});

test("CLI reports where a generated initial password is stored", async () => {
  const parent = await fs.promises.mkdtemp("/private/tmp/cs-password-output-");
  const home = path.join(parent, "managed");
  const previousSkip = process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
  const previousPassword = process.env.CONTROL_PASSWORD;
  const originalLog = console.log;
  const messages = [];
  process.env.CONTROL_SERVER_TEST_SKIP_DEPS = "1";
  delete process.env.CONTROL_PASSWORD;
  console.log = (...args) => messages.push(args.join(" "));
  try {
    assert.equal(await runCli(["install", "--home", home, "--no-start"]), 0);
    assert.equal(
      messages.includes(
        `비밀번호 저장 위치: ${resolveLayout(home).envPath} (권한 0600)`,
      ),
      true,
    );
  } finally {
    console.log = originalLog;
    if (previousSkip === undefined) delete process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
    else process.env.CONTROL_SERVER_TEST_SKIP_DEPS = previousSkip;
    if (previousPassword === undefined) delete process.env.CONTROL_PASSWORD;
    else process.env.CONTROL_PASSWORD = previousPassword;
    await fs.promises.rm(parent, { recursive: true, force: true });
  }
});

test("managed home rejects broad destructive targets", () => {
  assert.throws(() => assertSafeManagedHome("/"), /unsafe/);
  assert.throws(() => assertSafeManagedHome(os.homedir()), /unsafe/);
  assert.equal(isWithin("/tmp/control", "/tmp/control/releases/1.5.0"), true);
  assert.equal(isWithin("/tmp/control", "/tmp/elsewhere"), false);
});

test("version comparison rejects accidental downgrade ordering", () => {
  assert.equal(compareVersions("1.5.0", "1.4.3"), 1);
  assert.equal(compareVersions("1.5.0-rc.1", "1.5.0"), -1);
  assert.equal(compareVersions("1.5.0-rc.10", "1.5.0-rc.2"), 1);
  assert.equal(compareVersions("1.5.0", "1.5.0"), 0);
});

test("health gate waits for the expected release version", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(
      JSON.stringify({ ok: true, version: calls === 1 ? "1.4.3" : "1.5.0" }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  };
  try {
    assert.equal(await waitForHealth("http://127.0.0.1:9000", 2_000, "1.5.0"), true);
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("LaunchAgent ownership accepts XML-escaped managed paths", async () => {
  const parent = await fs.promises.mkdtemp("/private/tmp/cs-escaped-&-");
  const home = path.join(parent, "managed & home");
  const fakeUserHome = path.join(parent, "user");
  const previousHome = process.env.HOME;
  const previousSkip = process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
  const previousLabel = process.env.CONTROL_LAUNCHD_LABEL;
  process.env.HOME = fakeUserHome;
  process.env.CONTROL_SERVER_TEST_SKIP_DEPS = "1";
  process.env.CONTROL_LAUNCHD_LABEL = `io.github.twkim96.control-server.escaped.${process.pid}`;
  try {
    const installed = await installManaged({ homeDir: home, start: false });
    const agent = path.join(
      fakeUserHome,
      "Library",
      "LaunchAgents",
      `${installed.layout.launchdLabel}.plist`,
    );
    await fs.promises.mkdir(path.dirname(agent), { recursive: true });
    await fs.promises.writeFile(
      agent,
      `<plist><string>${installed.layout.current.replaceAll("&", "&amp;")}</string></plist>`,
    );
    await installManaged({
      homeDir: home,
      start: true,
      activate: async () => {},
    });
  } finally {
    if (previousHome === undefined) delete process.env.HOME;
    else process.env.HOME = previousHome;
    if (previousSkip === undefined) delete process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
    else process.env.CONTROL_SERVER_TEST_SKIP_DEPS = previousSkip;
    if (previousLabel === undefined) delete process.env.CONTROL_LAUNCHD_LABEL;
    else process.env.CONTROL_LAUNCHD_LABEL = previousLabel;
    await fs.promises.rm(parent, { recursive: true, force: true });
  }
});

test("no-start install creates one release and preserves private data on uninstall", async () => {
  const parent = await fs.promises.mkdtemp("/private/tmp/cs-cli-");
  const home = path.join(parent, "managed");
  const previousSkip = process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
  const previousLabel = process.env.CONTROL_LAUNCHD_LABEL;
  process.env.CONTROL_SERVER_TEST_SKIP_DEPS = "1";
  process.env.CONTROL_LAUNCHD_LABEL = `io.github.twkim96.control-server.first-failure.${process.pid}`;
  try {
    const installed = await installManaged({ homeDir: home, port: 19000, start: false });
    assert.equal(installed.metadata.version, "1.5.0");
    assert.equal(fs.existsSync(path.join(installed.layout.current, "backend", "app.py")), true);
    assert.equal(fs.existsSync(path.join(installed.layout.current, "backend", "tests")), false);
    assert.equal(fs.existsSync(path.join(installed.layout.current, "backend", "config.yml")), false);
    assert.equal(fs.existsSync(path.join(installed.layout.current, "ops", "pm2", "node_modules")), false);
    assert.equal(fs.existsSync(installed.layout.configPath), true);
    assert.equal(fs.statSync(installed.layout.envPath).mode & 0o777, 0o600);
    assert.equal(fs.statSync(installed.layout.configPath).mode & 0o777, 0o600);
    assert.equal(fs.statSync(installed.layout.home).mode & 0o777, 0o700);
    assert.match(fs.readFileSync(installed.layout.envPath, "utf8"), /CONTROL_PASSWORD='[^']+'/);
    assert.match(fs.readFileSync(installed.layout.configPath, "utf8"), /port: 19000/);
    assert.equal(installed.layout.url, "http://127.0.0.1:19000");

    const inspection = await inspectInstallation(home);
    assert.equal(inspection.metadata.version, "1.5.0");
    assert.equal(inspection.checks.find((check) => check.name === "frontend").ok, true);
    assert.equal(inspection.checks.find((check) => check.name === "environment permissions").ok, true);

    await uninstallManaged({ homeDir: home, purge: false, stop: false });
    assert.equal(fs.existsSync(resolveLayout(home).configPath), true);
    assert.equal(fs.existsSync(resolveLayout(home).runtime), true);
    assert.equal(fs.existsSync(resolveLayout(home).current), false);
  } finally {
    if (previousSkip === undefined) delete process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
    else process.env.CONTROL_SERVER_TEST_SKIP_DEPS = previousSkip;
    if (previousLabel === undefined) delete process.env.CONTROL_LAUNCHD_LABEL;
    else process.env.CONTROL_LAUNCHD_LABEL = previousLabel;
    await fs.promises.rm(parent, { recursive: true, force: true });
  }
});

test("migration planning is read-only and explicit about downtime", () => {
  const plan = migrationPlan({ sourceRoot: "/tmp/source", homeDir: "/tmp/control-home" });
  assert.equal(plan.source, "/tmp/source");
  assert.equal(plan.target, "/tmp/control-home");
  assert.equal(plan.mappings.length, 4);
  assert.equal(plan.mappings[0].source, "/tmp/source/backend/config.yml");
  assert.equal(plan.requiresDowntime, true);
  assert.match(plan.note, /PM2_HOME/);
});

test("install refuses to replace a foreign LaunchAgent", async () => {
  const parent = await fs.promises.mkdtemp("/private/tmp/cs-foreign-");
  const home = path.join(parent, "managed");
  const fakeUserHome = path.join(parent, "user");
  const agents = path.join(fakeUserHome, "Library", "LaunchAgents");
  const previousHome = process.env.HOME;
  const previousSkip = process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
  process.env.HOME = fakeUserHome;
  process.env.CONTROL_SERVER_TEST_SKIP_DEPS = "1";
  await fs.promises.mkdir(agents, { recursive: true });
  await fs.promises.writeFile(
    path.join(agents, "com.twkim.server-control.plist"),
    "<plist><string>/another/install/backend/app.py</string></plist>",
  );
  try {
    await assert.rejects(
      installManaged({ homeDir: home, start: true }),
      /다른 설치 경로/,
    );
    assert.equal(fs.existsSync(resolveLayout(home).current), false);
    assert.equal(fs.existsSync(resolveLayout(home).metadataPath), false);
  } finally {
    if (previousHome === undefined) delete process.env.HOME;
    else process.env.HOME = previousHome;
    if (previousSkip === undefined) delete process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
    else process.env.CONTROL_SERVER_TEST_SKIP_DEPS = previousSkip;
    await fs.promises.rm(parent, { recursive: true, force: true });
  }
});

test("failed first activation unloads the new LaunchAgent and removes current", async () => {
  const parent = await fs.promises.mkdtemp("/private/tmp/cs-first-failure-");
  const home = path.join(parent, "managed");
  const previousSkip = process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
  const previousLabel = process.env.CONTROL_LAUNCHD_LABEL;
  process.env.CONTROL_SERVER_TEST_SKIP_DEPS = "1";
  process.env.CONTROL_LAUNCHD_LABEL = `io.github.twkim96.control-server.first-failure.${process.pid}`;
  const recoveryActions = [];
  try {
    await assert.rejects(
      installManaged({
        homeDir: home,
        start: true,
        activate: async ({ action }) => {
          assert.equal(action, "load");
          throw new Error("synthetic first activation failure");
        },
        rollbackActivate: async ({ action }) => {
          recoveryActions.push(action);
        },
      }),
      /synthetic first activation failure/,
    );

    const layout = resolveLayout(home);
    assert.deepEqual(recoveryActions, ["unload"]);
    assert.equal(fs.existsSync(layout.current), false);
    assert.equal(fs.existsSync(layout.metadataPath), false);
    assert.equal(fs.existsSync(layout.configPath), true);
  } finally {
    if (previousSkip === undefined) delete process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
    else process.env.CONTROL_SERVER_TEST_SKIP_DEPS = previousSkip;
    if (previousLabel === undefined) delete process.env.CONTROL_LAUNCHD_LABEL;
    else process.env.CONTROL_LAUNCHD_LABEL = previousLabel;
    await fs.promises.rm(parent, { recursive: true, force: true });
  }
});

test("re-running install restarts an existing managed installation", async () => {
  const parent = await fs.promises.mkdtemp("/private/tmp/cs-repeat-install-");
  const home = path.join(parent, "managed");
  const previousSkip = process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
  const previousLabel = process.env.CONTROL_LAUNCHD_LABEL;
  process.env.CONTROL_SERVER_TEST_SKIP_DEPS = "1";
  process.env.CONTROL_LAUNCHD_LABEL = `io.github.twkim96.control-server.repeat.${process.pid}`;
  try {
    await installManaged({ homeDir: home, start: false });
    let activationAction = null;
    await installManaged({
      homeDir: home,
      start: true,
      mode: "install",
      activate: async ({ action }) => {
        activationAction = action;
      },
    });

    assert.equal(activationAction, "restart");
    assert.equal(
      JSON.parse(await fs.promises.readFile(resolveLayout(home).metadataPath, "utf8")).version,
      "1.5.0",
    );
  } finally {
    if (previousSkip === undefined) delete process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
    else process.env.CONTROL_SERVER_TEST_SKIP_DEPS = previousSkip;
    if (previousLabel === undefined) delete process.env.CONTROL_LAUNCHD_LABEL;
    else process.env.CONTROL_LAUNCHD_LABEL = previousLabel;
    await fs.promises.rm(parent, { recursive: true, force: true });
  }
});

test("failed update restores current release and metadata", async () => {
  const parent = await fs.promises.mkdtemp("/private/tmp/cs-rollback-");
  const home = path.join(parent, "managed");
  const source = path.join(parent, "source-1.5.1");
  const previousSkip = process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
  const previousLabel = process.env.CONTROL_LAUNCHD_LABEL;
  process.env.CONTROL_SERVER_TEST_SKIP_DEPS = "1";
  process.env.CONTROL_LAUNCHD_LABEL = `io.github.twkim96.control-server.test.${process.pid}`;
  try {
    await installManaged({ homeDir: home, start: false });
    const configPath = resolveLayout(home).configPath;
    const configBefore = `${await fs.promises.readFile(configPath, "utf8")}\n# rollback-data-sentinel\n`;
    await fs.promises.writeFile(configPath, configBefore, { mode: 0o600 });
    await copyApplication(path.resolve(import.meta.dirname, "../.."), source);
    const packagePath = path.join(source, "package.json");
    const packagePayload = JSON.parse(await fs.promises.readFile(packagePath, "utf8"));
    packagePayload.version = "1.5.1";
    await fs.promises.writeFile(packagePath, `${JSON.stringify(packagePayload, null, 2)}\n`);
    let rollbackCalled = false;

    await assert.rejects(
      installManaged({
        homeDir: home,
        sourceRoot: source,
        start: true,
        mode: "update",
        activate: async () => {
          throw new Error("synthetic activation failure");
        },
        rollbackActivate: async () => {
          rollbackCalled = true;
        },
      }),
      /synthetic activation failure/,
    );

    const layout = resolveLayout(home);
    assert.equal(fs.readlinkSync(layout.current), path.join("releases", "1.5.0"));
    assert.equal(JSON.parse(await fs.promises.readFile(layout.metadataPath, "utf8")).version, "1.5.0");
    assert.equal(fs.existsSync(path.join(layout.releases, "1.5.1")), true);
    assert.equal(rollbackCalled, true);
    assert.equal(await fs.promises.readFile(layout.configPath, "utf8"), configBefore);
  } finally {
    if (previousSkip === undefined) delete process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
    else process.env.CONTROL_SERVER_TEST_SKIP_DEPS = previousSkip;
    if (previousLabel === undefined) delete process.env.CONTROL_LAUNCHD_LABEL;
    else process.env.CONTROL_LAUNCHD_LABEL = previousLabel;
    await fs.promises.rm(parent, { recursive: true, force: true });
  }
});

test("explicit rollback selects a completed older release", async () => {
  const parent = await fs.promises.mkdtemp("/private/tmp/cs-explicit-rollback-");
  const home = path.join(parent, "managed");
  const source = path.join(parent, "source-1.5.1");
  const previousSkip = process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
  const previousLabel = process.env.CONTROL_LAUNCHD_LABEL;
  process.env.CONTROL_SERVER_TEST_SKIP_DEPS = "1";
  process.env.CONTROL_LAUNCHD_LABEL = `io.github.twkim96.control-server.rollback.${process.pid}`;
  try {
    await installManaged({ homeDir: home, start: false });
    const configPath = resolveLayout(home).configPath;
    const configBefore = `${await fs.promises.readFile(configPath, "utf8")}\n# explicit-rollback-data-sentinel\n`;
    await fs.promises.writeFile(configPath, configBefore, { mode: 0o600 });
    await copyApplication(path.resolve(import.meta.dirname, "../.."), source);
    const packagePath = path.join(source, "package.json");
    const payload = JSON.parse(await fs.promises.readFile(packagePath, "utf8"));
    payload.version = "1.5.1";
    await fs.promises.writeFile(packagePath, `${JSON.stringify(payload, null, 2)}\n`);
    await installManaged({ homeDir: home, sourceRoot: source, start: false, mode: "update" });

    const result = await rollbackManaged({
      homeDir: home,
      toVersion: "1.5.0",
      activate: async () => {},
    });
    assert.equal(result.fromVersion, "1.5.1");
    assert.equal(result.toVersion, "1.5.0");
    const layout = resolveLayout(home);
    assert.equal(fs.readlinkSync(layout.current), path.join("releases", "1.5.0"));
    assert.equal(
      JSON.parse(await fs.promises.readFile(layout.metadataPath, "utf8")).version,
      "1.5.0",
    );
    assert.equal(await fs.promises.readFile(layout.configPath, "utf8"), configBefore);
  } finally {
    if (previousSkip === undefined) delete process.env.CONTROL_SERVER_TEST_SKIP_DEPS;
    else process.env.CONTROL_SERVER_TEST_SKIP_DEPS = previousSkip;
    if (previousLabel === undefined) delete process.env.CONTROL_LAUNCHD_LABEL;
    else process.env.CONTROL_LAUNCHD_LABEL = previousLabel;
    await fs.promises.rm(parent, { recursive: true, force: true });
  }
});
