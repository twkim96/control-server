from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from config_schema import (
    ActionConfig,
    HealthConfig,
    LifecycleConfig,
    LogConfig,
    ServiceConfig,
    StopStrategy,
)
from pm2_manager import Pm2Manager


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PM2_INTEGRATION") != "1",
    reason="set RUN_PM2_INTEGRATION=1 for the isolated real-PM2 test",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(port: int, expected: bool, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.15)
            open_now = sock.connect_ex(("127.0.0.1", port)) == 0
        if open_now is expected:
            return
        time.sleep(0.1)
    raise AssertionError(f"port {port} expected open={expected}")


def _service(tmp_path: Path, port: int) -> ServiceConfig:
    return ServiceConfig(
        id="pm2_fixture",
        name="PM2 fixture",
        description="",
        cwd=str(tmp_path),
        entry_file="listener.py",
        command=(sys.executable, "-u", "listener.py", str(port)),
        env={},
        port=port,
        port_env_name=None,
        open_url=None,
        health=HealthConfig(False, "tcp", None, 1.0, True),
        log=LogConfig(True, 100, 1024 * 1024, 1),
        lifecycle=LifecycleConfig("manual", False, "primary", "primary", "status_only"),
        actions=(
            ActionConfig(
                "stop",
                "Stop",
                "process_stop",
                True,
                StopStrategy("SIGINT", 3.0, False, ("SIGTERM", "SIGKILL")),
            ),
        ),
    )


def test_real_pm2_lifecycle_isolated(tmp_path: Path) -> None:
    listener = tmp_path / "listener.py"
    listener.write_text(
        "import signal, socket, sys, time\n"
        "running = True\n"
        "def stop(*_):\n"
        "    global running\n"
        "    running = False\n"
        "signal.signal(signal.SIGINT, stop)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "sock = socket.socket()\n"
        "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "sock.bind((\"127.0.0.1\", int(sys.argv[1])))\n"
        "sock.listen()\n"
        "print(\"ready\", flush=True)\n"
        "while running:\n"
        "    time.sleep(0.05)\n"
        "sock.close()\n",
        encoding="utf-8",
    )
    port = _free_port()
    service = _service(tmp_path, port)
    wrapper = Path(__file__).resolve().parents[2] / "scripts" / "pm2ctl.sh"
    # macOS AF_UNIX paths are length-limited. pytest's nested tmp_path can make
    # PM2_HOME/rpc.sock exceed that limit, so keep the daemon home deliberately short.
    pm2_runtime = Path(tempfile.mkdtemp(prefix="sc-pm2-", dir="/private/tmp"))
    manager = Pm2Manager(
        pm2_runtime,
        tmp_path / "logs",
        wrapper=wrapper,
        command_timeout_seconds=15.0,
        snapshot_ttl_seconds=0.1,
    )
    pm2_home = pm2_runtime / "pm2"

    try:
        first = manager.start(service)
        assert first.pid is not None
        _wait_port(port, True)

        os.kill(first.pid, signal.SIGKILL)
        deadline = time.monotonic() + 12
        restarted = None
        while time.monotonic() < deadline:
            state = manager.get_process(service.id, force=True)
            if state and state.pid and state.pid != first.pid and state.alive:
                restarted = state
                break
            time.sleep(0.2)
        assert restarted is not None
        _wait_port(port, True)

        stopped = manager.stop(service.id)
        assert stopped is not None and stopped.status == "stopped"
        _wait_port(port, False)

        again = manager.restart(service)
        assert again.alive
        _wait_port(port, True)

        manager.stop(service.id)
        _wait_port(port, False)
        manager.delete(service.id)
        assert manager.get_process(service.id, force=True) is None
    finally:
        env = os.environ.copy()
        env["CONTROL_PM2_HOME"] = str(pm2_home)
        subprocess.run(
            [str(wrapper), "kill"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        shutil.rmtree(pm2_runtime, ignore_errors=True)
