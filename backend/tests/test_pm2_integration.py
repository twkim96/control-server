from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest
import psutil

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
        log_path = tmp_path / "logs" / "pm2_fixture.log"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (
            not log_path.exists() or "ready" not in log_path.read_text(encoding="utf-8")
        ):
            time.sleep(0.05)
        assert "ready" in log_path.read_text(encoding="utf-8")
        pm2_log_dir = pm2_home / "logs"
        assert not list(pm2_log_dir.glob("*-out.log"))
        assert not list(pm2_log_dir.glob("*-error.log"))

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
        assert stopped.pid is None
        assert manager.get_process(service.id, force=True).status == "stopped"
        _wait_port(port, False)

        started_again = manager.start(service)
        assert started_again.alive
        _wait_port(port, True)
        manager.stop(service.id)
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


def test_real_pm2_stop_does_not_leave_signal_ignoring_child(tmp_path: Path) -> None:
    parent = tmp_path / "parent.py"
    child_pid_file = tmp_path / "child.pid"
    parent.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "print('child', child.pid, flush=True)\n"
        "while True: time.sleep(0.1)\n",
        encoding="utf-8",
    )
    service = replace(
        _service(tmp_path, _free_port()),
        id="pm2_tree_fixture",
        command=(sys.executable, "-u", "parent.py", str(child_pid_file)),
        port=None,
    )
    wrapper = Path(__file__).resolve().parents[2] / "scripts" / "pm2ctl.sh"
    pm2_runtime = Path(tempfile.mkdtemp(prefix="sc-pm2-tree-", dir="/private/tmp"))
    manager = Pm2Manager(
        pm2_runtime,
        tmp_path / "logs",
        wrapper=wrapper,
        command_timeout_seconds=15.0,
        snapshot_ttl_seconds=0.1,
    )
    pm2_home = pm2_runtime / "pm2"
    child_pid = None
    try:
        manager.start(service)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.05)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        assert psutil.pid_exists(child_pid)

        manager.stop(service.id)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and psutil.pid_exists(child_pid):
            time.sleep(0.1)
        assert not psutil.pid_exists(child_pid)
        manager.delete(service.id)
    finally:
        if child_pid is not None and psutil.pid_exists(child_pid):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
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


def test_real_pm2_stop_delivers_sigterm_fallback_to_child(tmp_path: Path) -> None:
    parent = tmp_path / "fallback_parent.py"
    child = tmp_path / "fallback_child.py"
    child_pid_file = tmp_path / "fallback_child.pid"
    graceful_marker = tmp_path / "fallback_child.graceful"
    child.write_text(
        "import pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "def stop(*_):\n"
        "    pathlib.Path(sys.argv[1]).write_text('SIGTERM')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "while True: time.sleep(0.1)\n",
        encoding="utf-8",
    )
    parent.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, sys.argv[2], sys.argv[3]])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "while True: time.sleep(0.1)\n",
        encoding="utf-8",
    )
    service = replace(
        _service(tmp_path, _free_port()),
        id="pm2_fallback_fixture",
        command=(
            sys.executable,
            "-u",
            "fallback_parent.py",
            str(child_pid_file),
            str(child),
            str(graceful_marker),
        ),
        port=None,
    )
    strategy = service.actions[0].stop_strategy
    assert strategy is not None
    wrapper = Path(__file__).resolve().parents[2] / "scripts" / "pm2ctl.sh"
    pm2_runtime = Path(tempfile.mkdtemp(prefix="sc-pm2-fallback-", dir="/private/tmp"))
    manager = Pm2Manager(
        pm2_runtime,
        tmp_path / "logs",
        wrapper=wrapper,
        command_timeout_seconds=15.0,
        snapshot_ttl_seconds=0.1,
    )
    pm2_home = pm2_runtime / "pm2"
    child_pid = None
    try:
        manager.activate_service(service)
        manager.start(service)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.05)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))

        manager.stop(service, strategy)

        assert graceful_marker.read_text(encoding="utf-8") == "SIGTERM"
        assert not psutil.pid_exists(child_pid)
        manager.delete(service.id)
    finally:
        if child_pid is not None and psutil.pid_exists(child_pid):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
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


def test_real_pm2_live_log_rotation_preserves_pid_and_output(tmp_path: Path, monkeypatch) -> None:
    import gzip
    # Use the selected engine for compatibility with its 7.1 log flush fixes,
    # but always run against a separate, short PM2_HOME.
    selected = Path.home() / ".control-server/runtime/pm2-engine/current/node_modules/pm2/bin/pm2"
    if not selected.exists():
        pytest.skip("PM2 7.1 selected engine needed for live rotation acceptance")
    version = __import__("json").loads((selected.resolve().parent.parent / "package.json").read_text())["version"]
    if tuple(map(int, version.split(".")[:3])) < (7, 1, 0):
        pytest.skip("PM2 >=7.1 required")
    monkeypatch.setenv("CONTROL_PM2_ENGINE_CLI", str(selected))
    (tmp_path / "writer.py").write_text(
        "import time,signal,sys\nsignal.signal(signal.SIGINT, lambda *_: sys.exit(0))\nfor i in range(1000):\n print(f'line-{i}', flush=True)\n time.sleep(0.03)\n"
    )
    service = replace(_service(tmp_path, _free_port()), command=(sys.executable, "-u", "writer.py"), port=None)
    service = replace(service, log=replace(service.log, max_bytes=30, keep=2, compress=True, rotate_while_running=True))
    wrapper = Path(__file__).resolve().parents[2] / "scripts/pm2ctl.sh"
    runtime = Path(tempfile.mkdtemp(prefix="sc-live-", dir="/private/tmp"))
    manager = Pm2Manager(runtime, tmp_path / "logs", wrapper=wrapper)
    manager.activate_service(service)
    try:
        before = manager.start(service)
        path = manager._log.log_path(service.id)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and (not path.exists() or path.stat().st_size < 30):
            time.sleep(0.03)
        assert manager.rotate_running_logs((service,)) == 1
        time.sleep(0.2)
        after = manager.get_process(service.id, force=True)
        assert after.pid == before.pid
        manager.stop(service, service.actions[0].stop_strategy)
        pieces = []
        for archived in sorted(path.parent.glob(f"{service.id}.log.*")):
            if archived.suffix == ".gz":
                pieces.append(gzip.decompress(archived.read_bytes()).decode())
            elif archived.suffix[1:].isdigit():
                pieces.append(archived.read_text())
        if path.exists():
            pieces.append(path.read_text())
        numbers = sorted(int(line.removeprefix("line-")) for text in pieces for line in text.splitlines())
        assert numbers and numbers == list(range(numbers[-1] + 1))
    finally:
        env = {**os.environ, "CONTROL_PM2_HOME": str(runtime / "pm2")}
        result = subprocess.run([str(wrapper), "kill"], env=env, capture_output=True, timeout=20)
        assert result.returncode == 0
        shutil.rmtree(runtime)


def test_daemon_signal_recovery_preserves_running_and_stopped_intent(tmp_path: Path) -> None:
    """A real dedicated daemon exits; the controller restores only its lost app."""
    from types import SimpleNamespace
    from health_checker import HealthChecker
    from service_operations import ServiceOperations

    listener = tmp_path / "listener.py"
    listener.write_text(
        "import signal,socket,sys,time\n"
        "running=True\n"
        "def stop(*_):\n global running\n running=False\n"
        "signal.signal(signal.SIGINT,stop)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        "sock=socket.socket()\n"
        "sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
        "sock.bind(('127.0.0.1',int(sys.argv[1])))\n"
        "sock.listen()\n"
        "while running: time.sleep(0.05)\n"
        "sock.close()\n", encoding="utf-8")
    active = _service(tmp_path, _free_port())
    stopped_port = _free_port()
    stopped = replace(_service(tmp_path, stopped_port), id="stopped_fixture")
    registry = SimpleNamespace(list_services=lambda: (active, stopped),
                               get=lambda sid: active if sid == active.id else stopped)
    runtime = Path(tempfile.mkdtemp(prefix="sc-recover-", dir="/private/tmp"))
    wrapper = Path(__file__).resolve().parents[2] / "scripts/pm2ctl.sh"
    manager = Pm2Manager(runtime, tmp_path / "logs", wrapper=wrapper,
                         snapshot_ttl_seconds=0.1)
    health = HealthChecker(manager)
    ops = ServiceOperations(registry, manager, health, runtime)
    tracked = []
    try:
        first = manager.start(active)
        tracked.append(psutil.Process(first.pid))
        _wait_port(active.port, True)
        manager.start(stopped)
        manager.stop(stopped)
        ops.poll_once()
        old_id = manager.daemon_identity()
        assert old_id is not None
        daemon_pid = int((runtime / "pm2/pm2.pid").read_text())
        os.kill(daemon_pid, signal.SIGTERM)
        _wait_port(active.port, False)
        psutil.wait_procs([psutil.Process(daemon_pid)], timeout=12)
        assert not psutil.pid_exists(daemon_pid)
        # Recreate the controller too: recovery relies on its private disk state.
        ops = ServiceOperations(registry, manager, health, runtime)
        ops.poll_once()
        restored = manager.get_process(active.id, force=True)
        assert restored is not None and restored.alive and restored.pid != first.pid
        tracked.append(psutil.Process(restored.pid))
        assert manager.daemon_identity() != old_id
        _wait_port(active.port, True)
        _wait_port(stopped.port, False)
        assert manager.get_process(stopped.id, force=True) is None
        assert ops.recovery_status(active.id)["status"] == "restored"
        manager.stop(active)
        ops.record_action(active.id, "stop")
        assert ops.recovery_status(active.id)["desired_state"] == "stopped"
    finally:
        ops.shutdown()
        manager.shutdown()
        health._executor.shutdown(wait=True)
        env = os.environ.copy()
        env["CONTROL_PM2_HOME"] = str(runtime / "pm2")
        subprocess.run([str(wrapper), "kill"], env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False)
        for proc in tracked:
            try:
                if proc.is_running():
                    proc.kill()
            except psutil.NoSuchProcess:
                pass
        shutil.rmtree(runtime, ignore_errors=True)
