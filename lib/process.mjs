import { spawnSync } from "node:child_process";

export function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env || process.env,
    encoding: "utf8",
    stdio: options.capture ? "pipe" : "inherit",
  });
  if (result.error) {
    throw new Error(`${command} 실행 실패: ${result.error.message}`);
  }
  if (result.status !== 0 && !options.allowFailure) {
    const detail = options.capture
      ? String(result.stderr || result.stdout || "").trim()
      : "";
    throw new Error(
      `${command} ${args.join(" ")} 실패 (exit ${result.status})${detail ? `: ${detail}` : ""}`,
    );
  }
  return result;
}

export function commandPath(command) {
  const result = spawnSync("/usr/bin/which", [command], {
    encoding: "utf8",
    stdio: "pipe",
  });
  if (result.status !== 0) return null;
  const value = String(result.stdout || "").trim();
  return value || null;
}

export function parseVersion(output) {
  const match = String(output).match(/(\d+)\.(\d+)(?:\.(\d+))?/);
  if (!match) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3] || 0)];
}

export function versionAtLeast(actual, minimum) {
  for (let index = 0; index < Math.max(actual.length, minimum.length); index += 1) {
    const left = actual[index] || 0;
    const right = minimum[index] || 0;
    if (left !== right) return left > right;
  }
  return true;
}
