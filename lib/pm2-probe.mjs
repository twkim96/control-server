// Invoked only through pm2ctl.sh so engine maintenance and CLI locks apply.
import { createRequire } from "node:module";
import path from "node:path";

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
    const list = await call("list");
    const safe = list.filter(item => item.name?.startsWith("server-control--")).map(item => ({
      name: item.name, pid: item.pid || 0,
      pm2_env: Object.fromEntries(["status", "pm_uptime", "restart_time", "exit_code", "kill_timeout"]
        .map(key => [key, item.pm2_env?.[key]])),
    }));
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
    }));
    process.stdout.write(`${JSON.stringify({ version, processes })}\n`);
  }
} catch {
  process.stderr.write("PM2 상태를 확인하지 못했습니다.\n");
  process.exitCode = 1;
} finally {
  pm2.disconnect();
}
