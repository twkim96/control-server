"""PM2 log rotation entrypoint; pm2ctl holds maintenance and CLI locks throughout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil

from log_manager import LogManager


def rotate(node, probe, cli, home, log_root, policies):
    version = json.loads((Path(cli).resolve().parent.parent / "package.json").read_text())["version"]
    if tuple(int(part) for part in version.split(".")[:3]) < (7, 1, 0):
        raise ValueError("live rotation requires PM2 7.1.0 or newer")
    manager = LogManager(log_root)
    prepared = []
    for policy in policies:
        sid = policy["id"]
        if not sid or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in sid):
            raise ValueError("invalid service id")
        if manager.prepare_live_rotation(sid, max_bytes=policy["max_bytes"]):
            prepared.append(policy)
    if not prepared:
        return 0
    subprocess.run([node, probe, cli, "reload-logs"], check=True, capture_output=True, timeout=20)
    pid = int((Path(home) / "pm2.pid").read_text().strip())
    daemon = psutil.Process(pid)
    deadline = time.monotonic() + 3
    pending = {os.path.realpath(manager.pending_path(p["id"])) for p in prepared}
    while True:
        opened = {os.path.realpath(entry.path) for entry in daemon.open_files()}
        if not pending.intersection(opened):
            break
        if time.monotonic() >= deadline:
            # Keep all pending data, even across controller restarts.
            return 0
        time.sleep(0.05)
    for policy in prepared:
        manager.mark_live_handoff(policy["id"])
    for policy in prepared:
        manager.finish_live_rotation(policy["id"], keep=policy["keep"], compress=policy["compress"])
    return len(prepared)


if __name__ == "__main__":
    os.umask(0o077)
    try:
        print(json.dumps({"rotated": rotate(*sys.argv[1:6], json.loads(sys.argv[6]))}))
    except Exception:
        # Do not reveal raw PM2 output, command env, or local log content.
        print("PM2 log rotation failed; pending logs retained", file=sys.stderr)
        sys.exit(1)
