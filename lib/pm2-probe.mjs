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
  const version = await call("getVersion");
  const processes = (await call("list")).map((item) => ({
    name: item.name,
    status: item.pm2_env?.status || "unknown",
    pid: item.pid || 0,
  }));
  process.stdout.write(`${JSON.stringify({ version, processes })}\n`);
} catch {
  process.stderr.write("PM2 상태를 확인하지 못했습니다.\n");
  process.exitCode = 1;
} finally {
  pm2.disconnect();
}
