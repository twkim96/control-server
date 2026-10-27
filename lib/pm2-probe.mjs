// Invoked only through pm2ctl.sh so engine maintenance and CLI locks apply.
import { createRequire } from "node:module";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const pm2 = require(path.resolve(process.argv[2], "../.."));
const call = (method) => new Promise((resolve, reject) => {
  pm2[method]((error, result) => error ? reject(error) : resolve(result));
});
try {
  await call("connect");
  if (process.argv[3] === "reload-logs") {
    await call("reloadLogs");
  } else if (process.argv[3] === "runtime") {
    const pidFile = path.join(process.env.PM2_HOME, "pm2.pid");
    const identity = () => {
      try {
        const fd = fs.openSync(pidFile, "r");
        try {
          const stat = fs.fstatSync(fd);
          const pid = fs.readFileSync(fd, "utf8").trim();
          if (!/^[1-9][0-9]*$/.test(pid)) return null;
          if (fs.statSync(pidFile).ino !== stat.ino) throw new Error("PID file replaced");
          return `${pid}:${stat.mtimeMs}`;
        } finally { fs.closeSync(fd); }
      } catch (error) {
        if (error.code === "ENOENT" || error.code === "EACCES") return null;
        throw error;
      }
    };
    const daemonId = identity();
    const list = await call("list");
    if (list.some(item => !item.name?.startsWith("server-control--"))) {
      throw new Error("Unsupported PM2 membership");
    }
    const safe = list.filter(item => item.name?.startsWith("server-control--")).map(item => ({
      name: item.name, pid: item.pid || 0,
      pm2_env: Object.fromEntries(["status", "pm_uptime", "restart_time", "exit_code", "kill_timeout", "autostart"]
        .map(key => [key, item.pm2_env?.[key]])),
    }));
    // The PID file is written for each daemon boot. Pair its timestamp with the
    // PID so a recycled PID cannot hide a replacement from the controller.
    if (identity() !== daemonId) throw new Error("PM2 daemon changed during observation");
    safe.push({ control_pm2_daemon_id: daemonId });
    await new Promise((resolve, reject) => process.stdout.write(`${JSON.stringify(safe)}\n`, error => error ? reject(error) : resolve()));
    // A read-only CLI has no pending work after its single result is flushed.
    // PM2's socket reconnect timers must not keep this one-shot probe alive.
    pm2.disconnect();
    process.exit(0);
  } else {
    const version = await call("getVersion");
    const processes = (await call("list")).map((item) => ({
      name: item.name,
      status: item.pm2_env?.status || "unknown",
      pid: item.pid || 0,
      options: Object.fromEntries(["exec_mode", "instances", "watch", "cron_restart", "pmx_module"]
        .map(key => [key, item.pm2_env?.[key]])),
    }));
    process.stdout.write(`${JSON.stringify({ version, processes })}\n`);
  }
} catch {
  process.stderr.write("PM2 상태를 확인하지 못했습니다.\n");
  process.exitCode = 1;
} finally {
  pm2.disconnect();
}
