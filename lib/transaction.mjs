import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { assertSafeManagedHome } from "./layout.mjs";

function canonicalPath(target) {
  try {
    return fs.realpathSync(target);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    return path.join(canonicalPath(path.dirname(target)), path.basename(target));
  }
}

// Keep the lock outside the installation: uninstall --purge must not unlink a
// live lock while another process is waiting to use the same managed home.
export async function withManagedLock(layout, callback) {
  const home = assertSafeManagedHome(canonicalPath(path.resolve(layout.home)));
  // A fixed macOS location keeps callers with different TMPDIR values together.
  const directory = path.join("/private/tmp", `control-server-locks-${process.getuid()}`);
  fs.mkdirSync(directory, { mode: 0o700, recursive: true });
  const directoryStat = fs.lstatSync(directory);
  if (!directoryStat.isDirectory() || directoryStat.uid !== process.getuid() || (directoryStat.mode & 0o077)) {
    throw new Error(`Unsafe Control Server lock directory: ${directory}`);
  }
  const lock = path.join(directory, `${crypto.createHash("sha256").update(home).digest("hex")}.lock`);
  return withFileLock(lock, callback);
}

// lockf locks the shared open-file description inherited as descriptor 3. Node
// retains that descriptor after lockf exits, so close (including process death)
// releases the kernel lock. Other spawned commands never receive this fd: only
// this lockf call explicitly includes it in stdio. Keep the file permanently;
// unlinking it would let another caller lock a different inode at the same path.
export async function withFileLock(lockPath, callback) {
  const directory = fs.lstatSync(path.dirname(lockPath));
  if (!directory.isDirectory() || directory.uid !== process.getuid() || (directory.mode & 0o077)) {
    throw new Error(`Unsafe Control Server lock directory: ${path.dirname(lockPath)}`);
  }
  const fd = fs.openSync(lockPath, fs.constants.O_CREAT | fs.constants.O_RDWR | fs.constants.O_NOFOLLOW, 0o600);
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.uid !== process.getuid() || stat.nlink !== 1 || (stat.mode & 0o077)) {
      throw new Error(`Unsafe Control Server transaction lock: ${lockPath}`);
    }
    const result = spawnSync("/usr/bin/lockf", ["-s", "-t", "0", "3"], {
      encoding: "utf8", stdio: ["ignore", "pipe", "pipe", fd],
    });
    if (result.error) throw result.error;
    if (result.status !== 0) {
      const error = new Error(`Control Server transaction busy or lock unavailable: ${lockPath}${result.stderr?.trim() ? ` (${result.stderr.trim()})` : ""}`);
      error.code = result.status === 75 ? "CONTROL_LOCK_BUSY" : "CONTROL_LOCK_UNAVAILABLE";
      throw error;
    }
    return await callback();
  } finally {
    fs.closeSync(fd);
  }
}

export async function atomicWriteJson(file, payload) {
  const temporary = `${file}.tmp-${process.pid}-${crypto.randomUUID()}`;
  try {
    await fs.promises.writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, { mode: 0o600, flag: "wx" });
    await fs.promises.rename(temporary, file);
  } finally {
    await fs.promises.rm(temporary, { force: true });
  }
}
