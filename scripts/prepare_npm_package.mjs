import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const frontend = spawnSync("npm", ["run", "build"], {
  cwd: path.join(root, "frontend"),
  stdio: "inherit",
});
if (frontend.status !== 0) process.exit(frontend.status || 1);

const lock = path.join(root, "ops", "pm2", "package-lock.json");
const shrinkwrap = path.join(root, "ops", "pm2", "npm-shrinkwrap.json");
fs.copyFileSync(lock, shrinkwrap);

if (!fs.existsSync(path.join(root, "frontend", "dist", "index.html"))) {
  throw new Error("frontend/dist/index.html was not built");
}
