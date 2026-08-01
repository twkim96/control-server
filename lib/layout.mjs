import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const PACKAGE_ROOT = path.resolve(
  fileURLToPath(new URL("..", import.meta.url)),
);

export function defaultControlHome(env = process.env) {
  return path.resolve(env.CONTROL_SERVER_HOME || path.join(os.homedir(), ".control-server"));
}

export function resolveLayout(homeDir = defaultControlHome(), options = {}) {
  const home = path.resolve(homeDir);
  const releases = path.join(home, "releases");
  const config = path.join(home, "config");
  const runtime = path.join(home, "runtime");
  return {
    home,
    releases,
    current: path.join(home, "current"),
    configDir: config,
    configPath: path.join(config, "config.yml"),
    envPath: path.join(config, "run.env"),
    runtime,
    pm2Home: path.join(runtime, "pm2"),
    logs: path.join(home, "logs"),
    metadataPath: path.join(home, "install.json"),
    launchdLabel: options.launchdLabel || process.env.CONTROL_LAUNCHD_LABEL || "com.twkim.server-control",
    url:
      options.url ||
      process.env.CONTROL_SERVER_URL ||
      `http://127.0.0.1:${options.port || process.env.CONTROL_SERVER_PORT || 9000}`,
  };
}

export function assertSafeManagedHome(homeDir) {
  const home = path.resolve(homeDir);
  const filesystemRoot = path.parse(home).root;
  if (home === filesystemRoot || home === os.homedir()) {
    throw new Error(`unsafe CONTROL_SERVER_HOME: ${home}`);
  }
  if (home.length < filesystemRoot.length + 4) {
    throw new Error(`CONTROL_SERVER_HOME is too broad: ${home}`);
  }
  return home;
}

export function isWithin(parent, candidate) {
  const relative = path.relative(path.resolve(parent), path.resolve(candidate));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}
