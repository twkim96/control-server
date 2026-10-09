import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import os from "node:os";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { atomicWriteJson, withFileLock, withManagedLock } from "./transaction.mjs";

const VERSION = /^\d+\.\d+\.\d+$/;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const readJson = (file) => {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; }
};

async function withMaintenanceLock(lockPath, callback) {
  const deadline = Date.now() + 10_000;
  for (;;) {
    try { return await withFileLock(lockPath, callback); }
    catch (error) {
      if (error.code !== "CONTROL_LOCK_BUSY" || Date.now() >= deadline) throw error;
      await delay(100);
    }
  }
}

export function engineLayout(runtimeDir) {
  const runtime = path.resolve(runtimeDir);
  const root = path.join(runtime, "pm2-engine");
  return {
    runtime, root, home: path.dirname(runtime),
    pm2Home: path.join(runtime, "pm2"),
    releases: path.join(root, "releases"),
    current: path.join(root, "current"),
    job: path.join(root, "job.json"),
    latest: path.join(root, "latest.json"),
    recovery: path.join(root, "recovery.json"),
    maintenanceLock: path.join(root, "maintenance.lock"),
    owner: path.join(root, "maintenance-owner"),
  };
}

export function packageVersion(cli) {
  return readJson(path.resolve(cli, "../../package.json"))?.version || null;
}

export function selectedCli(layout, appRoot) {
  const external = path.join(layout.current, "node_modules/pm2/bin/pm2");
  return fs.existsSync(external) ? external :
    (process.env.CONTROL_PM2_CLI || path.join(appRoot, "ops/pm2/node_modules/pm2/bin/pm2"));
}

export async function checkLatest(layout, fetchImpl = fetch) {
  const response = await fetchImpl("https://registry.npmjs.org/pm2/latest", {
    signal: AbortSignal.timeout(8_000),
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("최신 PM2 버전을 확인하지 못했습니다.");
  const metadata = await response.json();
  if (metadata.name !== "pm2" || !VERSION.test(metadata.version)) {
    throw new Error("PM2 배포 정보가 올바르지 않습니다.");
  }
  await atomicWriteJson(layout.latest, {
    version: metadata.version, checked_at: new Date().toISOString(),
  });
  return metadata.version;
}

export function runCaptured(command, args, { env = process.env, timeout = 30_000, input } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { env, stdio: ["pipe", "pipe", "pipe"], detached: true });
    let stdout = "";
    let size = 0;
    let failure;
    const stop = () => {
      try { process.kill(-child.pid, "SIGKILL"); } catch { /* already exited */ }
    };
    const timer = setTimeout(() => { failure = new Error("명령 처리 시간이 초과됐습니다."); stop(); }, timeout);
    child.stdout.on("data", (chunk) => {
      size += chunk.length;
      if (size > 8 * 1024 * 1024) { failure = new Error("명령 출력 한도를 초과했습니다."); stop(); }
      else stdout += chunk.toString();
    });
    child.stderr.on("data", () => {}); // Never copy inherited credentials or commands into job status.
    child.on("error", () => { clearTimeout(timer); reject(new Error("업데이트 도구를 실행하지 못했습니다.")); });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (failure || code !== 0) reject(failure || new Error(`업데이트 명령이 실패했습니다 (exit ${code}).`));
      else resolve(stdout);
    });
    child.stdin.on("error", () => {});
    child.stdin.end(input);
  });
}

function snapshotFromOutput(output) {
  // PM2 may emit an informational line when it opens its first connection.
  const line = output.trim().split("\n").reverse().find((value) => value.startsWith('{"version":'));
  const result = JSON.parse(line || output);
  if (typeof result.version !== "string" || !Array.isArray(result.processes)) {
    throw new Error("PM2 상태 응답이 올바르지 않습니다.");
  }
  return result;
}

export function validateSnapshot(snapshot) {
  const names = new Set();
  for (const item of snapshot.processes) {
    if (typeof item.name !== "string" || !/^server-control--[A-Za-z0-9][A-Za-z0-9._-]*$/.test(item.name) || names.has(item.name)) {
      throw new Error("전용 PM2에 예상하지 못한 서비스가 있어 업데이트를 중단했습니다.");
    }
    if (!["online", "stopped", "errored"].includes(item.status)) {
      throw new Error("상태가 변경 중인 서비스가 있습니다. 잠시 후 다시 시도하세요.");
    }
    if (item.status === "online" && !(item.pid > 0)) throw new Error("실행 중인 서비스의 상태를 확인하지 못했습니다.");
    names.add(item.name);
  }
}

export function statesMatch(before, after) {
  if (before.processes.length !== after.processes.length) return false;
  return before.processes.every((old) => {
    const next = after.processes.find((item) => item.name === old.name);
    return next && (old.status === "online"
      ? next.status === "online" && next.pid > 0
      : ["stopped", "errored"].includes(next.status) && !next.pid);
  });
}

async function pointCurrent(layout, target) {
  if (target === null) {
    await fs.promises.rm(layout.current, { force: true });
    return;
  }
  const temp = `${layout.current}.${crypto.randomUUID()}.tmp`;
  try {
    await fs.promises.symlink(target, temp);
    await fs.promises.rename(temp, layout.current);
  } finally { await fs.promises.rm(temp, { force: true }); }
}

function probeHealth(target) {
  return new Promise((resolve) => {
    const timeout = Math.min(5_000, Math.max(100, (target.timeout_seconds || 2) * 1000));
    if (target.type === "tcp") {
      const socket = net.createConnection({ host: target.host || "127.0.0.1", port: target.port });
      socket.setTimeout(timeout);
      const finish = (ok) => { socket.destroy(); resolve(ok); };
      socket.once("connect", () => finish(true));
      socket.once("error", () => finish(false));
      socket.once("timeout", () => finish(false));
      return;
    }
    try {
      const url = new URL(target.url);
      const transport = url.protocol === "https:" ? https : http;
      const request = transport.get(url, { rejectUnauthorized: target.verify_ssl !== false }, (response) => {
        response.resume();
        resolve(response.statusCode >= 200 && response.statusCode < 400);
      });
      request.setTimeout(timeout, () => request.destroy());
      request.once("error", () => resolve(false));
    } catch { resolve(false); }
  });
}

export async function updateEngine(options) {
  const { runtimeDir, appRoot, configPath, jobId = crypto.randomUUID(), hooks = {} } = options;
  const layout = engineLayout(runtimeDir);
  const execute = hooks.execute || runCaptured;
  const now = () => new Date().toISOString();
  if (layout.pm2Home === path.join(os.homedir(), ".pm2")) throw new Error("사용자 기본 PM2는 업데이트할 수 없습니다.");
  await fs.promises.mkdir(layout.root, { recursive: true, mode: 0o700 });
  await fs.promises.chmod(layout.root, 0o700);
  const node = options.nodeBin || process.execPath;
  const npm = options.npmBin || path.join(path.dirname(node), "npm");
  let job = { id: jobId, pid: process.pid, status: "running", phase: "checking", message: "업데이트를 준비하고 있습니다.", started_at: now() };
  const progress = async (phase, message, extra = {}) => {
    job = { ...job, phase, message, ...extra };
    await atomicWriteJson(layout.job, job);
  };
  try {
    return await withManagedLock({ home: layout.home }, async () => withMaintenanceLock(layout.maintenanceLock, async () => {
      const token = crypto.randomUUID();
      await fs.promises.writeFile(layout.owner, `${token}\n`, { mode: 0o600 });
      const env = { ...process.env, CONTROL_PM2_HOME: layout.pm2Home, CONTROL_PM2_NODE: node, CONTROL_PM2_ENGINE_TOKEN: token };
      const command = async (cli, args, timeout = 30_000) => execute(path.join(appRoot, "scripts/pm2ctl.sh"), args, {
        env: { ...env, CONTROL_PM2_ENGINE_CLI: cli }, timeout,
      });
      const snapshot = async (cli) => hooks.snapshot ? hooks.snapshot(cli) : snapshotFromOutput(await command(cli, ["__engine_snapshot"]));
      const healthy = hooks.probeHealth || probeHealth;
      const verify = async (cli, before, expected, targets) => {
        const deadline = Date.now() + (hooks.verifyTimeout ?? 45_000);
        do {
          const after = await snapshot(cli);
          if (after.version === expected && statesMatch(before, after) && (await Promise.all(targets.map(healthy))).every(Boolean)) return after;
          await delay(hooks.pollInterval ?? 1000);
        } while (Date.now() < deadline);
        throw new Error("업데이트 후 서비스 복구를 확인하지 못했습니다.");
      };
      const preserveStopped = async (cli, before) => {
        const after = await snapshot(cli);
        for (const old of before.processes.filter((item) => item.status !== "online")) {
          const current = after.processes.find((item) => item.name === old.name);
          if (current && (current.pid > 0 || !["stopped", "errored"].includes(current.status))) {
            await command(cli, ["stop", old.name], 180_000);
          }
        }
      };
      const restore = async (recovery) => {
        await progress("rolling_back", "이전 엔진과 서비스 상태를 복구하고 있습니다.");
        // Restore through PM2's supported update/resurrect lifecycle, not a second supervisor.
        await command(recovery.previous_cli, ["update"], 180_000);
        await fs.promises.copyFile(recovery.dump, path.join(layout.pm2Home, "dump.pm2"));
        await command(recovery.previous_cli, ["resurrect"], 180_000);
        const restored = await snapshot(recovery.previous_cli);
        for (const old of recovery.before.processes.filter((item) => item.status === "online")) {
          const current = restored.processes.find((item) => item.name === old.name);
          if (current && (current.status !== "online" || !current.pid)) {
            await command(recovery.previous_cli, ["restart", old.name], 180_000);
          }
        }
        await preserveStopped(recovery.previous_cli, recovery.before);
        await verify(recovery.previous_cli, recovery.before, recovery.previous_version, recovery.health_targets);
        await pointCurrent(layout, recovery.previous_target);
        await fs.promises.rm(layout.recovery, { force: true });
      };
      try {
        await progress("checking", "현재 엔진과 서비스 상태를 확인하고 있습니다.");
        const interrupted = readJson(layout.recovery);
        if (fs.existsSync(layout.recovery) && !interrupted) throw new Error("이전 업데이트 복구 기록을 읽지 못했습니다.");
        if (interrupted) {
          await restore(interrupted);
          await progress("complete", "중단됐던 업데이트를 복구했습니다. 다시 업데이트할 수 있습니다.", { status: "failed", rolled_back: true, finished_at: now() });
          return job;
        }
        const oldCli = fs.realpathSync(selectedCli(layout, appRoot));
        const before = await snapshot(oldCli);
        validateSnapshot(before);
        const rollbackCli = [oldCli,
          path.join(layout.releases, before.version, "node_modules/pm2/bin/pm2"),
          path.join(appRoot, "ops/pm2/node_modules/pm2/bin/pm2"),
        ].find((cli) => fs.existsSync(cli) && packageVersion(cli) === before.version);
        if (!rollbackCli) throw new Error("현재 실행 중인 엔진의 복구용 설치본을 찾지 못했습니다.");
        const latest = hooks.latest ? await hooks.latest() : await checkLatest(layout);
        if (!VERSION.test(latest)) throw new Error("PM2 버전이 올바르지 않습니다.");
        await progress("checking", "최신 PM2 엔진을 확인했습니다.", { from_version: before.version, target_version: latest });
        if (before.version === latest && packageVersion(oldCli) === latest) {
          await progress("complete", "이미 최신 PM2 엔진을 사용 중입니다.", { status: "succeeded", finished_at: now() });
          return job;
        }
        await progress("installing", "새 PM2 엔진을 설치하고 있습니다. 서비스는 계속 실행됩니다.");
        const target = path.join(layout.releases, latest);
        const candidateCli = path.join(target, "node_modules/pm2/bin/pm2");
        if (hooks.install) await hooks.install(target, latest);
        else if (packageVersion(candidateCli) !== latest || !fs.existsSync(path.join(target, ".complete.json"))) {
          const staging = path.join(layout.root, `.staging-${jobId}`);
          try {
            await fs.promises.mkdir(staging, { recursive: true, mode: 0o700 });
            await execute(npm, ["install", "--prefix", staging, "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund", "--save-exact", `pm2@${latest}`], {
              env: { ...env, npm_config_engine_strict: "true", npm_config_cache: path.join(layout.root, "npm-cache"), PATH: `${path.dirname(node)}:${process.env.PATH || ""}` }, timeout: 180_000,
            });
            if (packageVersion(path.join(staging, "node_modules/pm2/bin/pm2")) !== latest) throw new Error("설치된 PM2 버전 검증에 실패했습니다.");
            await atomicWriteJson(path.join(staging, ".complete.json"), { version: latest, installed_at: now() });
            await fs.promises.mkdir(layout.releases, { recursive: true, mode: 0o700 });
            if (fs.existsSync(target)) throw new Error("기존 엔진 설치가 불완전합니다. 복구가 필요합니다.");
            await fs.promises.rename(staging, target);
          } finally { await fs.promises.rm(staging, { recursive: true, force: true }); }
        }
        let targets;
        if (hooks.healthTargets) targets = await hooks.healthTargets();
        else {
          const python = process.env.CONTROL_PYTHON || path.join(appRoot, ".venv/bin/python");
          targets = JSON.parse(await execute(python, [path.join(appRoot, "backend/pm2_engine.py"), "health-targets", configPath], { timeout: 10_000 }));
        }
        targets = targets.filter((target) => before.processes.some((item) => item.name === target.name && item.status === "online"));
        const results = await Promise.all(targets.map(healthy));
        const healthTargets = targets.filter((_, index) => results[index]);
        // Refresh after installation: only the cutover snapshot is authoritative.
        const finalBefore = await snapshot(oldCli);
        validateSnapshot(finalBefore);
        if (!statesMatch(before, finalBefore)) throw new Error("준비 중 서비스 상태가 변경됐습니다. 다시 시도하세요.");
        await command(oldCli, ["save", "--force"]);
        const backupDir = path.join(layout.root, "backups", jobId);
        await fs.promises.mkdir(backupDir, { recursive: true, mode: 0o700 });
        const dump = path.join(backupDir, "dump.pm2");
        await fs.promises.copyFile(path.join(layout.pm2Home, "dump.pm2"), dump);
        await fs.promises.chmod(dump, 0o600);
        const recovery = {
          before: finalBefore, previous_cli: fs.realpathSync(rollbackCli), previous_version: before.version,
          previous_target: rollbackCli !== oldCli ? path.resolve(rollbackCli, "../../../..") :
            (fs.existsSync(layout.current) ? fs.realpathSync(layout.current) : null),
          dump, health_targets: healthTargets,
        };
        await atomicWriteJson(layout.recovery, recovery);
        try {
          await progress("updating", "PM2 엔진을 교체하고 서비스를 복원하고 있습니다.");
          await command(candidateCli, ["update"], 180_000);
          await preserveStopped(candidateCli, finalBefore);
          await progress("verifying", "엔진 버전과 서비스 응답을 확인하고 있습니다.");
          await verify(candidateCli, finalBefore, latest, healthTargets);
          await pointCurrent(layout, target);
          await fs.promises.rm(layout.recovery, { force: true });
          await progress("complete", "PM2 엔진 업데이트와 서비스 확인을 완료했습니다.", {
            status: "succeeded", finished_at: now(), health_checked: healthTargets.length,
            preexisting_unhealthy: targets.length - healthTargets.length,
          });
          return job;
        } catch (error) {
          try {
            await restore(recovery);
            await progress("complete", "업데이트에 실패해 이전 엔진과 서비스 상태로 복구했습니다.", { status: "failed", error: error.message, rolled_back: true, finished_at: now() });
          } catch {
            await progress("complete", "자동 복구를 완료하지 못했습니다. 업데이트 버튼으로 복구를 다시 시도하세요.", { status: "failed", rolled_back: false, finished_at: now() });
          }
          return job;
        }
      } finally { await fs.promises.rm(layout.owner, { force: true }); }
    }));
  } catch (error) {
    const existing = readJson(layout.job);
    if (existing?.id !== jobId && existing?.status === "running") {
      try {
        process.kill(existing.pid, 0);
        return { ...job, status: "failed", message: "다른 관리 작업이 진행 중입니다.", finished_at: now() };
      } catch { /* The previous writer is gone; publish this attempt's error. */ }
    }
    await progress("complete", "PM2 업데이트를 시작하지 못했습니다. 잠시 후 다시 시도하세요.", { status: "failed", error: /lock|잠금|busy/i.test(error.message) ? "다른 관리 작업이 진행 중입니다." : error.message, finished_at: now() });
    return job;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [operation, runtimeDir, appRoot, configPath, jobId] = process.argv.slice(2);
  try {
    const layout = engineLayout(runtimeDir);
    await fs.promises.mkdir(layout.root, { recursive: true, mode: 0o700 });
    if (operation === "check") process.stdout.write(`${JSON.stringify({ version: await checkLatest(layout) })}\n`);
    else if (operation === "update") {
      const result = await updateEngine({ runtimeDir, appRoot, configPath, jobId });
      process.exitCode = result.status === "succeeded" ? 0 : 1;
    } else throw new Error("지원하지 않는 엔진 명령입니다.");
  } catch { process.stderr.write("PM2 엔진 작업에 실패했습니다.\n"); process.exitCode = 1; }
}
