import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { readFile } from "node:fs/promises";

import { PACKAGE_ROOT, assertSafeManagedHome, isWithin, resolveLayout } from "./layout.mjs";
import { commandPath, parseVersion, run, versionAtLeast } from "./process.mjs";

const APP_COPY_ENTRIES = [
  "bin",
  "lib",
  "backend",
  "frontend/dist",
  "launchd",
  "ops/pm2",
  "scripts/pm2ctl.sh",
  "API.md",
  "PM2_RECOVERY.md",
  "README.md",
  "THIRD_PARTY_NOTICES.md",
  "LICENSE",
  "LICENSES",
  "docs",
  "SECURITY.md",
  "CONTRIBUTING.md",
  "CHANGELOG.md",
  "package.json",
];

const BACKEND_EXCLUDES = new Set([
  "__pycache__",
  ".pytest_cache",
  "tests",
  "logs",
  "runtime",
  "config.yml",
  "config.yml.bak",
  "requirements-dev.txt",
]);

const OPS_EXCLUDES = new Set(["node_modules", "package-lock.json"]);
const LAUNCHD_EXCLUDES = new Set(["run.env"]);

export function compareVersions(left, right) {
  const parse = (value) => {
    const match = String(value).match(/^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/);
    if (!match) throw new Error(`지원하지 않는 버전 형식: ${value}`);
    return {
      numbers: [Number(match[1]), Number(match[2]), Number(match[3])],
      prerelease: match[4] || null,
    };
  };
  const a = parse(left);
  const b = parse(right);
  for (let index = 0; index < 3; index += 1) {
    if (a.numbers[index] !== b.numbers[index]) {
      return a.numbers[index] > b.numbers[index] ? 1 : -1;
    }
  }
  if (a.prerelease === b.prerelease) return 0;
  if (a.prerelease === null) return 1;
  if (b.prerelease === null) return -1;
  return a.prerelease.localeCompare(b.prerelease, "en", { numeric: true });
}

function launchAgentPath(layout) {
  return path.join(process.env.HOME || os.homedir(), "Library", "LaunchAgents", `${layout.launchdLabel}.plist`);
}

function assertRuntimePathLength(layout) {
  const socketPath = path.join(layout.pm2Home, "interactor.sock");
  if (Buffer.byteLength(socketPath) >= 104) {
    throw new Error(`PM2 runtime 경로가 macOS socket 제한보다 깁니다: ${layout.pm2Home}`);
  }
}

function launchAgentBelongsToLayout(layout) {
  const plist = launchAgentPath(layout);
  if (!fs.existsSync(plist)) return true;
  try {
    const xmlPath = layout.current
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
    return fs.readFileSync(plist, "utf8").includes(xmlPath);
  } catch {
    return false;
  }
}

function assertNoForeignLaunchAgent(layout) {
  if (!launchAgentBelongsToLayout(layout)) {
    throw new Error(
      `${layout.launchdLabel} LaunchAgent가 다른 설치 경로를 사용 중입니다. ` +
        "기존 인스턴스를 자동으로 덮어쓰지 않습니다. 먼저 control-server migrate --plan을 확인하세요.",
    );
  }
}

export async function packageMetadata(sourceRoot = PACKAGE_ROOT) {
  const payload = JSON.parse(
    await readFile(path.join(sourceRoot, "package.json"), "utf8"),
  );
  return { name: payload.name, version: payload.version };
}

function shouldCopy(sourceRoot, sourcePath) {
  const relative = path.relative(sourceRoot, sourcePath);
  const parts = relative.split(path.sep);
  if (parts.some((part) => part === "node_modules" || part === "__pycache__")) {
    return false;
  }
  if (parts[0] === "backend" && parts.some((part) => BACKEND_EXCLUDES.has(part))) {
    return false;
  }
  if (parts[0] === "ops" && parts[1] === "pm2" && parts.some((part) => OPS_EXCLUDES.has(part))) {
    return false;
  }
  if (parts[0] === "launchd" && parts.some((part) => LAUNCHD_EXCLUDES.has(part))) {
    return false;
  }
  if (parts[0] === "frontend" && parts[1] !== "dist") return false;
  return true;
}

export async function copyApplication(sourceRoot, destination) {
  for (const relative of APP_COPY_ENTRIES) {
    const source = path.join(sourceRoot, relative);
    if (!fs.existsSync(source)) {
      throw new Error(`배포 파일이 없습니다: ${relative}`);
    }
    const target = path.join(destination, relative);
    await fs.promises.mkdir(path.dirname(target), { recursive: true });
    await fs.promises.cp(source, target, {
      recursive: true,
      dereference: false,
      filter: (candidate) => shouldCopy(sourceRoot, candidate),
    });
  }
  await fs.promises.chmod(path.join(destination, "launchd", "install.sh"), 0o755);
  await fs.promises.chmod(path.join(destination, "scripts", "pm2ctl.sh"), 0o755);
}

export function discoverDependencies(env = process.env) {
  if (process.platform !== "darwin") {
    throw new Error("Control Server 배포판은 현재 macOS만 지원합니다.");
  }
  const node = commandPath("node") || process.execPath;
  const npm = commandPath("npm");
  const python = env.CONTROL_SERVER_PYTHON || commandPath("python3");
  if (!node) throw new Error("Node.js를 찾지 못했습니다.");
  if (!npm) throw new Error("npm을 찾지 못했습니다.");
  if (!python) throw new Error("Python 3를 찾지 못했습니다.");

  const nodeResult = run(node, ["--version"], { capture: true });
  const pythonResult = run(python, ["--version"], { capture: true });
  const nodeVersion = parseVersion(nodeResult.stdout || nodeResult.stderr);
  const pythonVersion = parseVersion(pythonResult.stdout || pythonResult.stderr);
  if (!nodeVersion || !versionAtLeast(nodeVersion, [22, 0, 0])) {
    throw new Error("Node.js 22 이상이 필요합니다.");
  }
  if (!pythonVersion || !versionAtLeast(pythonVersion, [3, 10, 0])) {
    throw new Error("Python 3.10 이상이 필요합니다.");
  }
  return { node, npm, python };
}

async function writeInitialConfig(layout, sourceRoot) {
  if (!fs.existsSync(layout.configPath)) {
    const example = await readFile(
      path.join(sourceRoot, "backend", "config.example.yml"),
      "utf8",
    );
    const root = JSON.stringify(process.env.HOME || path.dirname(layout.home));
    const parsedUrl = new URL(layout.url);
    const port = parsedUrl.port || (parsedUrl.protocol === "https:" ? "443" : "80");
    const content = example
      .replace('    - "/path/to/projects"', `    - ${root}`)
      .replace("  port: 9000", `  port: ${port}`);
    await fs.promises.writeFile(layout.configPath, content, { mode: 0o600 });
  }
  await fs.promises.chmod(layout.configPath, 0o600);
  if (!fs.existsSync(layout.envPath)) {
    const password = process.env.CONTROL_PASSWORD || crypto.randomBytes(24).toString("base64url");
    if (/[\r\n\0]/.test(password)) {
      throw new Error("CONTROL_PASSWORD에는 줄바꿈이나 NUL을 사용할 수 없습니다.");
    }
    const shellValue = `'${password.replaceAll("'", `'"'"'`)}'`;
    const content = [
      "# Generated by control-server install. Keep this file private.",
      `CONTROL_PASSWORD=${shellValue}`,
      "CONTROL_PROCESS_BACKEND=pm2",
      "",
    ].join("\n");
    await fs.promises.writeFile(layout.envPath, content, { mode: 0o600 });
    return { generatedPassword: process.env.CONTROL_PASSWORD ? null : password };
  }
  await fs.promises.chmod(layout.envPath, 0o600);
  return { generatedPassword: null };
}

async function prepareRelease({ layout, sourceRoot, metadata, dependencies, skipDeps }) {
  const releaseDir = path.join(layout.releases, metadata.version);
  const marker = path.join(releaseDir, ".control-server-release.json");
  if (fs.existsSync(marker)) return releaseDir;

  const staging = path.join(layout.releases, `.${metadata.version}.staging-${process.pid}`);
  if (!isWithin(layout.releases, staging)) throw new Error("unsafe staging path");
  await fs.promises.rm(staging, { recursive: true, force: true });
  await fs.promises.mkdir(staging, { recursive: true });
  try {
    await copyApplication(sourceRoot, staging);
    if (!skipDeps) {
      run(dependencies.python, ["-m", "venv", path.join(staging, ".venv")]);
      run(
        path.join(staging, ".venv", "bin", "python"),
        [
          "-m",
          "pip",
          "install",
          "--disable-pip-version-check",
          "-r",
          path.join(staging, "backend", "requirements-runtime.lock"),
        ],
      );
      run(dependencies.npm, ["ci", "--omit=dev", "--prefix", path.join(staging, "ops", "pm2")]);
    } else {
      await fs.promises.mkdir(path.join(staging, ".venv", "bin"), { recursive: true });
      await fs.promises.writeFile(path.join(staging, ".venv", "bin", "python"), "", { mode: 0o755 });
    }
    await fs.promises.writeFile(
      path.join(staging, ".control-server-release.json"),
      `${JSON.stringify({ ...metadata, prepared_at: new Date().toISOString() }, null, 2)}\n`,
    );
    if (fs.existsSync(releaseDir)) {
      await fs.promises.rm(releaseDir, { recursive: true, force: true });
    }
    await fs.promises.rename(staging, releaseDir);
  } catch (error) {
    await fs.promises.rm(staging, { recursive: true, force: true });
    throw error;
  }
  return releaseDir;
}

function currentTarget(layout) {
  try {
    return fs.readlinkSync(layout.current);
  } catch {
    return null;
  }
}

async function switchCurrent(layout, releaseDir) {
  const next = `${layout.current}.next-${process.pid}`;
  await fs.promises.rm(next, { force: true });
  await fs.promises.symlink(path.relative(layout.home, releaseDir), next);
  await fs.promises.rename(next, layout.current);
}

async function restoreCurrent(layout, target) {
  if (!target) {
    await fs.promises.rm(layout.current, { force: true });
    return;
  }
  const next = `${layout.current}.rollback-${process.pid}`;
  await fs.promises.rm(next, { force: true });
  await fs.promises.symlink(target, next);
  await fs.promises.rename(next, layout.current);
}

export function launchdEnvironment(layout, dependencies = discoverDependencies()) {
  return {
    ...process.env,
    CONTROL_APP_ROOT: layout.current,
    CONTROL_ENV_FILE: layout.envPath,
    CONTROL_CONFIG_PATH: layout.configPath,
    CONTROL_LOG_DIR: layout.logs,
    CONTROL_RUNTIME_DIR: layout.runtime,
    CONTROL_FRONTEND_DIST: path.join(layout.current, "frontend", "dist"),
    CONTROL_PYTHON: path.join(layout.current, ".venv", "bin", "python"),
    CONTROL_PM2_NODE: dependencies.node,
    CONTROL_PM2_CLI: path.join(layout.current, "ops", "pm2", "node_modules", "pm2", "bin", "pm2"),
    CONTROL_PM2_HOME: layout.pm2Home,
    CONTROL_PROCESS_BACKEND: "pm2",
    CONTROL_LAUNCHD_LABEL: layout.launchdLabel,
  };
}

export function runLaunchd(layout, action, dependencies) {
  return run("bash", [path.join(layout.current, "launchd", "install.sh"), action], {
    env: launchdEnvironment(layout, dependencies),
    capture: action === "status",
    allowFailure: action === "status",
  });
}

function stopDedicatedPm2(layout, dependencies) {
  const wrapper = path.join(layout.current, "scripts", "pm2ctl.sh");
  if (
    !fs.existsSync(wrapper) ||
    !fs.existsSync(path.join(layout.current, "ops", "pm2", "node_modules"))
  ) {
    return;
  }
  run("bash", [wrapper, "delete", "all"], {
    env: launchdEnvironment(layout, dependencies),
    allowFailure: true,
  });
  run("bash", [wrapper, "kill"], {
    env: launchdEnvironment(layout, dependencies),
    allowFailure: true,
  });
}

export async function waitForHealth(url, timeoutMs = 20_000, expectedVersion = null) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(new URL("/api/meta", url), {
        signal: AbortSignal.timeout(2_000),
      });
      if (!response.ok) {
        lastError = new Error(`HTTP ${response.status}`);
      } else {
        const payload = await response.json();
        if (!expectedVersion || payload?.version === expectedVersion) return true;
        lastError = new Error(
          `unexpected Control Server version: ${payload?.version || "missing"} (expected ${expectedVersion})`,
        );
      }
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Control Server health 확인 실패: ${lastError?.message || "timeout"}`);
}

export async function installManaged({
  homeDir,
  port = null,
  sourceRoot = PACKAGE_ROOT,
  start = true,
  mode = "install",
  skipDeps = process.env.CONTROL_SERVER_TEST_SKIP_DEPS === "1",
  activate = null,
  rollbackActivate = null,
} = {}) {
  if (port !== null && (!Number.isInteger(port) || port < 1 || port > 65535)) {
    throw new Error(`유효하지 않은 port: ${port}`);
  }
  const layout = resolveLayout(
    assertSafeManagedHome(homeDir || resolveLayout().home),
    port === null ? {} : { port },
  );
  assertRuntimePathLength(layout);
  const requestedUrl = layout.url;
  const installedMetadata = await readInstallMetadata(layout);
  if (installedMetadata?.launchd_label) {
    layout.launchdLabel = installedMetadata.launchd_label;
  }
  if (installedMetadata?.url) layout.url = installedMetadata.url;
  if (start) assertNoForeignLaunchAgent(layout);
  const metadata = await packageMetadata(sourceRoot);
  const dependencies = skipDeps
    ? { node: commandPath("node") || process.execPath, npm: commandPath("npm") || "npm", python: commandPath("python3") || "python3" }
    : discoverDependencies();

  await fs.promises.mkdir(layout.home, { recursive: true, mode: 0o700 });
  await fs.promises.chmod(layout.home, 0o700);
  await fs.promises.mkdir(layout.releases, { recursive: true });
  await fs.promises.mkdir(layout.configDir, { recursive: true, mode: 0o700 });
  await fs.promises.mkdir(layout.runtime, { recursive: true, mode: 0o700 });
  await fs.promises.mkdir(layout.logs, { recursive: true, mode: 0o700 });
  await fs.promises.chmod(layout.configDir, 0o700);
  await fs.promises.chmod(layout.runtime, 0o700);
  await fs.promises.chmod(layout.logs, 0o700);

  const previousTarget = currentTarget(layout);
  const previousMetadata = installedMetadata;
  if (previousMetadata?.url) {
    if (port !== null && previousMetadata.url !== requestedUrl) {
      throw new Error("설치된 controller port는 update에서 변경하지 않습니다. config와 LaunchAgent를 별도로 변경하세요.");
    }
    layout.url = previousMetadata.url;
  }
  if (previousMetadata?.version && compareVersions(metadata.version, previousMetadata.version) < 0) {
    throw new Error(
      `downgrade는 자동으로 수행하지 않습니다: ${previousMetadata.version} -> ${metadata.version}`,
    );
  }
  const releaseDir = await prepareRelease({ layout, sourceRoot, metadata, dependencies, skipDeps });
  const initial = await writeInitialConfig(layout, sourceRoot);
  await switchCurrent(layout, releaseDir);
  const activationAction = mode === "update" || previousTarget ? "restart" : "load";

  const installMetadata = {
    schema_version: 1,
    package_name: metadata.name,
    version: metadata.version,
    current_release: releaseDir,
    url: layout.url,
    launchd_label: layout.launchdLabel,
    updated_at: new Date().toISOString(),
  };
  if (start) {
    try {
      assertNoForeignLaunchAgent(layout);
      if (activate) {
        await activate({ layout, dependencies, action: activationAction });
      } else {
        runLaunchd(layout, activationAction, dependencies);
        await waitForHealth(layout.url, 20_000, metadata.version);
      }
    } catch (error) {
      if (!previousTarget) {
        try {
          if (rollbackActivate) {
            await rollbackActivate({ layout, dependencies, action: "unload" });
          } else if (!activate) {
            runLaunchd(layout, "unload", dependencies);
            stopDedicatedPm2(layout, dependencies);
          }
        } catch {
          // Preserve the original activation failure; recovery evidence remains on disk.
        }
      }
      await restoreCurrent(layout, previousTarget);
      if (previousMetadata) {
        await fs.promises.writeFile(
          layout.metadataPath,
          `${JSON.stringify(previousMetadata, null, 2)}\n`,
        );
      } else {
        await fs.promises.rm(layout.metadataPath, { force: true });
      }
      if (previousTarget) {
        try {
          if (rollbackActivate) {
            await rollbackActivate({ layout, dependencies, action: "restart" });
          } else if (!activate) {
            runLaunchd(layout, "restart", dependencies);
          }
        } catch {
          // Preserve the original failure while leaving rollback evidence on disk.
        }
      }
      throw error;
    }
  }
  await fs.promises.writeFile(
    layout.metadataPath,
    `${JSON.stringify(installMetadata, null, 2)}\n`,
    { mode: 0o600 },
  );
  await fs.promises.chmod(layout.metadataPath, 0o600);
  return { layout, metadata, releaseDir, generatedPassword: initial.generatedPassword };
}

export async function readInstallMetadata(layout) {
  try {
    return JSON.parse(await readFile(layout.metadataPath, "utf8"));
  } catch {
    return null;
  }
}

export async function inspectInstallation(homeDir) {
  const layout = resolveLayout(assertSafeManagedHome(homeDir || resolveLayout().home));
  const metadata = await readInstallMetadata(layout);
  if (metadata?.launchd_label) layout.launchdLabel = metadata.launchd_label;
  if (metadata?.url) layout.url = metadata.url;
  let dependenciesOk = true;
  let dependenciesTarget = "Node >=22, Python >=3.10, npm";
  try {
    const dependencies = discoverDependencies();
    dependenciesTarget = `${dependencies.node}; ${dependencies.python}; ${dependencies.npm}`;
  } catch (error) {
    dependenciesOk = false;
    dependenciesTarget = error instanceof Error ? error.message : String(error);
  }
  const privateMode = (target) => {
    try {
      return (fs.statSync(target).mode & 0o077) === 0;
    } catch {
      return false;
    }
  };
  const checks = [
    ["dependencies", dependenciesOk, dependenciesTarget],
    ["metadata", Boolean(metadata), layout.metadataPath],
    ["current", fs.existsSync(layout.current), layout.current],
    ["config", fs.existsSync(layout.configPath), layout.configPath],
    ["environment", fs.existsSync(layout.envPath), layout.envPath],
    ["environment permissions", privateMode(layout.envPath), `${layout.envPath} (0600 expected)`],
    ["config permissions", privateMode(layout.configPath), `${layout.configPath} (private expected)`],
    ["python", fs.existsSync(path.join(layout.current, ".venv", "bin", "python")), path.join(layout.current, ".venv", "bin", "python")],
    ["frontend", fs.existsSync(path.join(layout.current, "frontend", "dist", "index.html")), path.join(layout.current, "frontend", "dist", "index.html")],
    ["pm2", fs.existsSync(path.join(layout.current, "ops", "pm2", "node_modules", "pm2", "bin", "pm2")), path.join(layout.current, "ops", "pm2", "node_modules", "pm2", "bin", "pm2")],
    ["launchd plist", fs.existsSync(launchAgentPath(layout)), launchAgentPath(layout)],
    [
      "launchd ownership",
      fs.existsSync(launchAgentPath(layout)) && launchAgentBelongsToLayout(layout),
      launchAgentPath(layout),
    ],
  ].map(([name, ok, target]) => ({ name, ok, target }));
  let online = false;
  let onlineReason = null;
  if (metadata && fs.existsSync(layout.current)) {
    try {
      await waitForHealth(metadata.url || layout.url, 2_500, metadata.version);
      online = true;
    } catch (error) {
      onlineReason = error instanceof Error ? error.message : String(error);
    }
  } else {
    onlineReason = "not installed";
  }
  return { layout, metadata, checks, online, onlineReason };
}

export async function uninstallManaged({ homeDir, purge = false, stop = true } = {}) {
  const layout = resolveLayout(assertSafeManagedHome(homeDir || resolveLayout().home));
  const metadata = await readInstallMetadata(layout);
  if (metadata?.launchd_label) layout.launchdLabel = metadata.launchd_label;
  if (metadata?.url) layout.url = metadata.url;
  if (stop && fs.existsSync(path.join(layout.current, "launchd", "install.sh"))) {
    assertNoForeignLaunchAgent(layout);
    const dependencies = discoverDependencies();
    stopDedicatedPm2(layout, dependencies);
    runLaunchd(layout, "unload", dependencies);
  }
  if (purge) {
    await fs.promises.rm(layout.home, { recursive: true, force: true });
    return { layout, purged: true };
  }
  await fs.promises.rm(layout.current, { force: true });
  await fs.promises.rm(layout.releases, { recursive: true, force: true });
  await fs.promises.rm(layout.metadataPath, { force: true });
  return { layout, purged: false };
}

export async function rollbackManaged({
  homeDir,
  toVersion = null,
  activate = null,
  rollbackActivate = null,
} = {}) {
  const layout = resolveLayout(assertSafeManagedHome(homeDir || resolveLayout().home));
  const metadata = await readInstallMetadata(layout);
  if (metadata?.launchd_label) layout.launchdLabel = metadata.launchd_label;
  if (metadata?.url) layout.url = metadata.url;
  if (!metadata?.version || !fs.existsSync(layout.current)) {
    throw new Error("rollback할 관리형 설치를 찾지 못했습니다.");
  }
  const releases = (await fs.promises.readdir(layout.releases, { withFileTypes: true }))
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
    .map((entry) => entry.name)
    .filter((version) => {
      try {
        return compareVersions(version, metadata.version) < 0;
      } catch {
        return false;
      }
    })
    .sort((left, right) => compareVersions(right, left));
  const selected = toVersion || releases[0];
  if (!selected || !releases.includes(selected)) {
    throw new Error(
      `rollback 가능한 이전 release가 없습니다${toVersion ? `: ${toVersion}` : ""}`,
    );
  }
  const targetRelease = path.join(layout.releases, selected);
  if (!fs.existsSync(path.join(targetRelease, ".control-server-release.json"))) {
    throw new Error(`완료되지 않은 release입니다: ${selected}`);
  }

  const previousTarget = currentTarget(layout);
  await switchCurrent(layout, targetRelease);
  const dependencies = discoverDependencies();
  try {
    assertNoForeignLaunchAgent(layout);
    if (activate) {
      await activate({ layout, dependencies, action: "restart" });
    } else {
      runLaunchd(layout, "restart", dependencies);
      await waitForHealth(metadata.url || layout.url, 20_000, selected);
    }
  } catch (error) {
    await restoreCurrent(layout, previousTarget);
    try {
      if (rollbackActivate) {
        await rollbackActivate({ layout, dependencies, action: "restart" });
      } else if (!activate) {
        runLaunchd(layout, "restart", dependencies);
      }
    } catch {
      // Keep the original rollback activation error.
    }
    throw error;
  }

  const nextMetadata = {
    ...metadata,
    version: selected,
    current_release: targetRelease,
    rolled_back_from: metadata.version,
    updated_at: new Date().toISOString(),
  };
  await fs.promises.writeFile(
    layout.metadataPath,
    `${JSON.stringify(nextMetadata, null, 2)}\n`,
    { mode: 0o600 },
  );
  return { layout, fromVersion: metadata.version, toVersion: selected };
}

export function migrationPlan({ sourceRoot = process.cwd(), homeDir } = {}) {
  const layout = resolveLayout(assertSafeManagedHome(homeDir || resolveLayout().home));
  const source = path.resolve(sourceRoot);
  const mappings = [
    ["backend/config.yml", layout.configPath],
    ["launchd/run.env", layout.envPath],
    ["backend/logs", layout.logs],
    ["backend/runtime", layout.runtime],
  ].map(([relative, target]) => ({
    source: path.join(source, relative),
    target,
    exists: fs.existsSync(path.join(source, relative)),
  }));
  return {
    source,
    target: layout.home,
    mappings,
    launch_agent: launchAgentPath(layout),
    launch_agent_conflict: !launchAgentBelongsToLayout(layout),
    requiresDowntime: true,
    note: "PM2_HOME 이동은 관리 서비스를 정상 종료한 승인된 cutover에서만 수행합니다.",
  };
}
