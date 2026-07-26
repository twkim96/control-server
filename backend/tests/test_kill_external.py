"""process_manager.kill_external 단위 테스트."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import psutil
import pytest

from config_loader import (
    HealthConfig,
    LifecycleConfig,
    LogConfig,
    ServiceConfig,
)
from log_manager import LogManager
from process_manager import ProcessError, ProcessManager


def _make_service(*, port: int) -> ServiceConfig:
    return ServiceConfig(
        id="external_target",
        name="external_target",
        description="",
        cwd="/tmp",
        entry_file="x.py",
        command=("python", "x.py"),
        env={},
        port=port,
        port_env_name=None,
        open_url=None,
        health=HealthConfig(enabled=False, type="none", url=None, timeout_seconds=2.0, verify_ssl=True),
        log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
        lifecycle=LifecycleConfig(
            mode="manual",
            autostart=False,
            stop_visibility="primary",
            restart_visibility="primary",
            unmanaged_policy="status_only",
        ),
        actions=(),
    )


def _spawn_external_listener(port: int) -> subprocess.Popen:
    """주어진 포트에 LISTEN하는 별도 프로세스를 띄운다.

    SIGINT로 깨끗히 종료되도록 signal handler를 등록한다.
    """
    code = f"""
import signal, socket, sys, time
running = True
def h(s, f):
    global running
    running = False
signal.signal(signal.SIGINT, h)
signal.signal(signal.SIGTERM, h)
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", {port}))
sock.listen(1)
sock.settimeout(0.1)
while running:
    try:
        conn, _ = sock.accept()
        conn.close()
    except socket.timeout:
        pass
sock.close()
sys.exit(0)
"""
    proc = subprocess.Popen([sys.executable, "-u", "-c", code])
    # 잠깐 대기해서 LISTEN 시작될 때까지
    for _ in range(20):
        time.sleep(0.05)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.1)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            return proc
        except OSError:
            continue
    raise RuntimeError(f"external listener on {port} failed to start")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def pm(tmp_path: Path) -> ProcessManager:
    lm = LogManager(tmp_path / "logs")
    return ProcessManager(tmp_path / "runtime", lm)


def test_kill_external_with_sigint(pm):
    port = _free_port()
    listener = _spawn_external_listener(port)
    try:
        service = replace(
            _make_service(port=port),
            cwd=psutil.Process(listener.pid).cwd(),
            command=tuple(psutil.Process(listener.pid).cmdline()),
        )
        result = pm.kill_external(service)
        assert result["pid"] == listener.pid
        # SIGINT만으로 깨끗히 죽었어야 함
        assert result["signal"] == "SIGINT"
        # zombie reaping을 위해 listener.wait() 호출하면 OS 입장에서도 사라짐
        listener.wait(timeout=5)
        assert not psutil.pid_exists(listener.pid)
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def test_kill_external_rejects_when_no_port(pm):
    service = _make_service(port=0)
    object.__setattr__(service, "port", None)  # frozen dataclass 우회
    with pytest.raises(ProcessError, match="port가 설정"):
        pm.kill_external(service)


def test_kill_external_rejects_when_no_holder(pm):
    """포트가 비어있으면 거부."""
    port = _free_port()
    service = _make_service(port=port)
    with pytest.raises(ProcessError, match="찾지 못했습니다"):
        pm.kill_external(service)


def test_kill_external_rejects_self_pid(pm):
    """컨트롤 서버 자신의 PID는 거부."""
    # 자기 자신 PID가 어떤 포트를 잡고 있는 척하기 위해
    # _find_port_holder를 monkeypatch 하는 게 깔끔.
    from process_manager import _find_port_holder  # noqa: F401
    import process_manager as pm_module

    own_pid = os.getpid()

    def fake_find(port):
        return {"pid": own_pid, "name": "control_server", "addr": ""}

    original = pm_module._find_port_holder
    pm_module._find_port_holder = fake_find
    try:
        service = _make_service(port=12345)
        with pytest.raises(ProcessError, match="자신을 종료"):
            pm.kill_external(service)
    finally:
        pm_module._find_port_holder = original


def test_kill_external_rejects_tracked_pid(pm, tmp_path):
    """컨트롤 서버가 추적 중인 다른 서비스 PID는 거부."""
    # 가짜 추적 상태를 PM에 주입
    from process_manager import RuntimeState

    fake_pid = 999999
    pm._states["other"] = RuntimeState(pid=fake_pid, pgid=fake_pid)

    import process_manager as pm_module

    def fake_find(port):
        return {"pid": fake_pid, "name": "tracked", "addr": ""}

    original = pm_module._find_port_holder
    pm_module._find_port_holder = fake_find
    try:
        service = _make_service(port=12345)
        with pytest.raises(ProcessError, match="추적 중인 다른 서비스"):
            pm.kill_external(service)
    finally:
        pm_module._find_port_holder = original


def test_kill_external_rejects_holder_change_before_first_signal(pm, monkeypatch):
    """health/holder 관찰 뒤 port owner가 바뀌면 signal 없이 409 성격 오류를 낸다."""
    import process_manager as pm_module

    class FakeProcess:
        def __init__(self, _pid):
            pass

        def create_time(self):
            return 10.0

        def cmdline(self):
            return ["python", "x.py"]

        def cwd(self):
            return "/tmp"

        def is_running(self):
            return True

        def status(self):
            return psutil.STATUS_RUNNING

    holders = iter(
        [
            {"pid": 12345, "name": "old", "addr": ""},
            {"pid": 54321, "name": "new", "addr": ""},
        ]
    )
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(pm_module, "_find_port_holder", lambda _port: next(holders))
    monkeypatch.setattr(pm_module.psutil, "Process", FakeProcess)
    monkeypatch.setattr(pm_module.os, "getpgid", lambda _pid: 12345)
    monkeypatch.setattr(pm_module.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    with pytest.raises(ProcessError, match="health 확인 뒤 변경"):
        pm.kill_external(_make_service(port=12345))
    assert sent == []
