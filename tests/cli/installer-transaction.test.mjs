import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { once } from "node:events";
import test from "node:test";

import { installManaged, rollbackManaged, uninstallManaged } from "../../lib/installer.mjs";
import { resolveLayout } from "../../lib/layout.mjs";
import { atomicWriteJson, withFileLock, withManagedLock } from "../../lib/transaction.mjs";

async function fixture(t) {
  const root = await fs.promises.mkdtemp("/private/tmp/cs-txn-");
  t.after(() => fs.promises.rm(root, { recursive: true, force: true }));
  const home = path.join(root, "managed");
  const layout = resolveLayout(home);
  for (const version of ["1.5.1", "1.5.2", "1.5.3"]) {
    const release = path.join(layout.releases, version);
    await fs.promises.mkdir(release, { recursive: true });
    await atomicWriteJson(path.join(release, "package.json"), { name: "audit", version });
    await atomicWriteJson(path.join(release, ".control-server-release.json"), { version });
  }
  await fs.promises.mkdir(layout.configDir);
  await fs.promises.writeFile(layout.configPath, "audit: true\n");
  await fs.promises.writeFile(layout.envPath, "# synthetic\n");
  await fs.promises.symlink("releases/1.5.2", layout.current);
  await atomicWriteJson(layout.metadataPath, { version: "1.5.2", launchd_label: `audit.transaction.${process.pid}` });
  return layout;
}

function state(layout) {
  return [fs.readlinkSync(layout.current), JSON.parse(fs.readFileSync(layout.metadataPath, "utf8")).version];
}

test("transaction excludes another process and reclaims its lock after death", async (t) => {
  const layout = await fixture(t);
  const moduleUrl = new URL("../../lib/transaction.mjs", import.meta.url).href;
  const child = spawn(process.execPath, ["--input-type=module", "-e", `
    import { withManagedLock } from ${JSON.stringify(moduleUrl)};
    await withManagedLock({ home: process.argv[1] }, async () => {
      process.stdout.write('locked\\n');
      await new Promise(() => setInterval(() => {}, 1000));
    });
  `, layout.home], { stdio: ["ignore", "pipe", "pipe"] });
  t.after(() => { if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL"); });
  const ready = await Promise.race([
    once(child.stdout, "data").then(([data]) => data.toString()),
    once(child, "exit").then(() => { throw new Error("lock holder exited before readiness"); }),
  ]);
  assert.equal(ready, "locked\n");
  await assert.rejects(withManagedLock(layout, async () => assert.fail("entered busy transaction")), /transaction busy/);
  const exited = once(child, "exit");
  child.kill("SIGKILL");
  await exited;
  let acquired = false;
  await withManagedLock(layout, async () => { acquired = true; });
  assert.equal(acquired, true);
});

test("failed update cannot interleave another update or uninstall", async (t) => {
  const layout = await fixture(t);
  await assert.rejects(installManaged({
    homeDir: layout.home, sourceRoot: path.join(layout.releases, "1.5.3"), skipDeps: true,
    activate: async () => {
      await assert.rejects(installManaged({ homeDir: layout.home, start: false, skipDeps: true }), /transaction busy/);
      await assert.rejects(uninstallManaged({ homeDir: layout.home, purge: true, stop: false }), /transaction busy/);
      throw new Error("activation failed");
    },
    rollbackActivate: async () => {},
  }), /activation failed/);
  assert.deepEqual(state(layout), ["releases/1.5.2", "1.5.2"]);
  await installManaged({ homeDir: layout.home, sourceRoot: path.join(layout.releases, "1.5.3"), skipDeps: true, start: false });
  assert.deepEqual(state(layout), ["releases/1.5.3", "1.5.3"]);
});

test("rollback dependency failure leaves the current release and metadata intact", async (t) => {
  const layout = await fixture(t);
  const previous = process.env.CONTROL_SERVER_PYTHON;
  process.env.CONTROL_SERVER_PYTHON = "/nonexistent/control-server-audit-python";
  try {
    await assert.rejects(rollbackManaged({ homeDir: layout.home, activate: async () => assert.fail("activation called") }), /실행 실패/);
    assert.deepEqual(state(layout), ["releases/1.5.2", "1.5.2"]);
  } finally {
    if (previous === undefined) delete process.env.CONTROL_SERVER_PYTHON;
    else process.env.CONTROL_SERVER_PYTHON = previous;
  }
});

for (const operation of ["update", "rollback"]) test(`${operation} metadata commit failure restores release and reactivates the previous version`, async (t) => {
  const layout = await fixture(t);
  let recovered = false;
  let failCommit = true;
  const original = fs.promises.rename;
  fs.promises.rename = async function (from, to) {
    if (to === layout.metadataPath && failCommit) {
      failCommit = false;
      throw new Error("synthetic ENOSPC metadata commit");
    }
    return original.call(this, from, to);
  };
  try {
    const callbacks = {
      activate: async () => {},
      rollbackActivate: async () => {
        assert.deepEqual(state(layout), ["releases/1.5.2", "1.5.2"]);
        recovered = true;
      },
    };
    await assert.rejects(operation === "update"
      ? installManaged({ homeDir: layout.home, sourceRoot: path.join(layout.releases, "1.5.3"), skipDeps: true, ...callbacks })
      : rollbackManaged({ homeDir: layout.home, ...callbacks }), /synthetic ENOSPC/);
    assert.equal(recovered, true);
    assert.deepEqual(state(layout), ["releases/1.5.2", "1.5.2"]);
    assert.equal(fs.statSync(layout.metadataPath).mode & 0o777, 0o600);
    assert.equal(fs.readdirSync(layout.home).some((file) => file.includes(".tmp-")), false);
  } finally { fs.promises.rename = original; }
});

test("activation error retains rollback failure evidence", async (t) => {
  const layout = await fixture(t);
  const original = new Error("original activation failure");
  await assert.rejects(installManaged({
    homeDir: layout.home, sourceRoot: path.join(layout.releases, "1.5.3"), skipDeps: true,
    activate: async () => { throw original; },
    rollbackActivate: async () => { throw new Error("recovery restart failure"); },
  }), (error) => {
    assert.equal(error.cause, original);
    assert.match(error.message, /original activation failure; rollback failed: recovery restart failure/);
    return true;
  });
});

test("purge releases its transaction lock and allows a fresh installation", async (t) => {
  const layout = await fixture(t);
  await uninstallManaged({ homeDir: layout.home, purge: true, stop: false });
  assert.equal(fs.existsSync(layout.home), false);
  await withManagedLock(layout, async () => {
    await fs.promises.mkdir(layout.home);
    await assert.rejects(withManagedLock(layout, async () => {}), /transaction busy/);
  });
  await withManagedLock(layout, async () => {});
});

test("file lock rejects a symlink without changing its target", async (t) => {
  const layout = await fixture(t);
  await fs.promises.chmod(layout.home, 0o700);
  const target = path.join(layout.home, "sentinel");
  const lock = path.join(layout.home, "maintenance.lock");
  await fs.promises.writeFile(target, "preserve me", { mode: 0o600 });
  await fs.promises.symlink(target, lock);
  await assert.rejects(withFileLock(lock, async () => assert.fail("entered unsafe lock")), { code: "ELOOP" });
  assert.equal(fs.readFileSync(target, "utf8"), "preserve me");
});

for (const shutdownFails of [false, true]) test(`uninstall stops polling before PM2 shutdown and preserves files on shutdown failure (${shutdownFails})`, async (t) => {
  const layout = await fixture(t);
  const release = fs.realpathSync(layout.current);
  await fs.promises.mkdir(path.join(release, "launchd"));
  await fs.promises.mkdir(path.join(release, "scripts"));
  await fs.promises.mkdir(path.join(release, "ops/pm2/node_modules"), { recursive: true });
  await fs.promises.mkdir(layout.runtime);
  const python = path.join(layout.home, "fixture-python");
  await fs.promises.writeFile(python, '#!/bin/sh\necho "Python 3.14.0"\n', { mode: 0o700 });
  const previous = process.env.CONTROL_SERVER_PYTHON;
  process.env.CONTROL_SERVER_PYTHON = python;
  t.after(() => {
    if (previous === undefined) delete process.env.CONTROL_SERVER_PYTHON;
    else process.env.CONTROL_SERVER_PYTHON = previous;
  });
  await fs.promises.writeFile(path.join(release, "launchd/install.sh"), `#!/bin/sh
test "$1" = unload || exit 2
touch "$CONTROL_RUNTIME_DIR/controller-unloaded"
`);
  await fs.promises.writeFile(path.join(release, "scripts/pm2ctl.sh"), `#!/bin/sh
test -f "$CONTROL_RUNTIME_DIR/controller-unloaded" || exit 3
test "$1" = kill || exit 4
exit ${shutdownFails ? 75 : 0}
`);
  if (shutdownFails) {
    await assert.rejects(uninstallManaged({ homeDir: layout.home, purge: true }), /exit 75/);
    assert.equal(fs.readFileSync(layout.configPath, "utf8"), "audit: true\n");
    assert.deepEqual(state(layout), ["releases/1.5.2", "1.5.2"]);
  } else {
    await uninstallManaged({ homeDir: layout.home, purge: true });
    assert.equal(fs.existsSync(layout.home), false);
  }
});
