"""Opt-in real engine upgrade; uses only an isolated short PM2_HOME and fixture ports."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from config_loader import load_config
from pm2_manager import Pm2Manager

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PM2_ENGINE_INTEGRATION") != "1",
    reason="set RUN_PM2_ENGINE_INTEGRATION=1 for isolated engine upgrade with npm download",
)


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_real_engine_upgrade_preserves_service_states_and_is_idempotent(monkeypatch):
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix="cs-eng-", dir="/private/tmp"))
    runtime = root / "runtime"
    node = shutil.which("node")
    assert node
    pinned = repo / "ops/pm2/node_modules/pm2/bin/pm2"
    monkeypatch.setenv("CONTROL_PM2_NODE", node)
    monkeypatch.setenv("CONTROL_PM2_CLI", str(pinned))
    monkeypatch.setenv("CONTROL_PYTHON", sys.executable)
    monkeypatch.delenv("CONTROL_PM2_ENGINE_CLI", raising=False)
    monkeypatch.delenv("CONTROL_PM2_ENGINE_TOKEN", raising=False)
    listener = root / "listener.py"
    listener.write_text("import http.server,sys\nhttp.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), http.server.SimpleHTTPRequestHandler).serve_forever()\n")
    ports = [_port(), _port()]
    while ports[0] == ports[1]:
        ports[1] = _port()
    raw = {"controller": {"host": "127.0.0.1", "port": _port()}, "services": []}
    for sid, port in zip(["engine_running", "engine_stopped"], ports):
        raw["services"].append({
            "id": sid, "name": sid, "cwd": str(root), "entry_file": "listener.py",
            "command": [sys.executable, "-u", str(listener), str(port)], "port": port,
            "health": {"enabled": True, "type": "tcp", "url": f"127.0.0.1:{port}", "timeout_seconds": 1},
        })
    config_path = root / "config.yml"
    config_path.write_text(json.dumps(raw))
    config = load_config(config_path)
    manager = Pm2Manager(runtime, root / "logs", command_timeout_seconds=25)
    env = {**os.environ, "CONTROL_PM2_HOME": str(runtime / "pm2")}
    try:
        stopped = config.services[1]
        manager.start(stopped)
        manager.stop(stopped)
        manager.start(config.services[0])
        command = [node, str(repo / "lib/pm2-engine.mjs"), "update", str(runtime), str(repo), str(config_path)]
        result = subprocess.run(command, env=env, capture_output=True, timeout=300, check=False)
        job = json.loads((runtime / "pm2-engine/job.json").read_text())
        assert result.returncode == 0, {k: job.get(k) for k in ["phase", "message", "error", "rolled_back"]}
        assert job["status"] == "succeeded"
        assert job["health_checked"] == 1
        probe = subprocess.run([str(repo / "scripts/pm2ctl.sh"), "__engine_snapshot"], env=env, capture_output=True, text=True, timeout=30, check=True)
        state = json.loads(probe.stdout.strip().splitlines()[-1])
        assert state["version"] == job["target_version"]
        running = next(p for p in state["processes"] if p["name"] == "server-control--engine_running")
        stopped_state = next(p for p in state["processes"] if p["name"] == "server-control--engine_stopped")
        assert running["status"] == "online" and running["pid"] > 0
        assert stopped_state["status"] in {"stopped", "errored"} and stopped_state["pid"] == 0
        result = subprocess.run(command, env=env, capture_output=True, timeout=45, check=False)
        assert result.returncode == 0
        manager.invalidate()
        after = manager.snapshot(force=True)
        assert after["engine_running"].pid == running["pid"]
        print(f"isolated PM2 {job['from_version']} -> {job['target_version']}; running=1 stopped=1; health=1; repeated update kept PID")
    finally:
        manager.shutdown()
        # Cleanup only this test's daemon, even if a failed recovery fence remains.
        current = runtime / "pm2-engine/current/node_modules/pm2/bin/pm2"
        subprocess.run([node, str(current if current.is_file() else pinned), "kill"],
                       env={**env, "PM2_HOME": str(runtime / "pm2")},
                       capture_output=True, timeout=45, check=False)
        deadline = time.monotonic() + 5
        pid_file = runtime / "pm2/pm2.pid"
        while pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not pid_file.exists(), "isolated daemon cleanup did not complete"
        shutil.rmtree(root)
