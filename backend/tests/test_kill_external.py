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


@pytest.fixture
def simulated_external(pm, monkeypatch):
    import process_manager as module

    pid = 12345
    state = {"birth": 10.0, "alive": True, "holder": True, "denied": False, "time": 0.0}
    sent = []

    class FakeProcess:
        def __init__(self, _pid):
            pass

        def create_time(self):
            if state["denied"]:
                raise psutil.AccessDenied(pid)
            return state["birth"]

        def is_running(self):
            return state["alive"]

        def status(self):
            return psutil.STATUS_RUNNING

    monkeypatch.setattr(module.psutil, "Process", FakeProcess)
    monkeypatch.setattr(module, "_find_port_holder", lambda _port: {"pid": pid} if state["holder"] else None)
    monkeypatch.setattr(module, "_verify_destructive_identity", lambda *_args: None)
    monkeypatch.setattr(module.os, "kill", lambda target, sig: sent.append((target, sig)))
    monkeypatch.setattr(module.time, "sleep", lambda delay: state.__setitem__("time", state["time"] + delay))
    pm._clock = lambda: state["time"]
    evaluation = module.AdoptEvaluation(
        diagnostics=module.AdoptDiagnostics(ok=True, reason="ok", candidate_pid=pid),
        candidate=module._AdoptCandidate(pid=pid, pgid=pid, create_time=10.0),
        state_snapshot=module.RuntimeState(),
    )
    return module, state, sent, evaluation


def test_kill_external_rejects_reused_pid_before_signal(pm, simulated_external):
    _, state, sent, evaluation = simulated_external
    state["birth"] = 10.02
    with pytest.raises(ProcessError, match="signal 전 변경"):
        pm.kill_external(_make_service(port=12345), evaluation=evaluation)
    assert sent == []


def test_kill_external_waits_for_process_after_listener_closes(pm, monkeypatch, simulated_external):
    module, state, sent, evaluation = simulated_external

    def send(pid, sig):
        sent.append((pid, sig))
        state["holder"] = False

    monkeypatch.setattr(module.os, "kill", send)
    with pytest.raises(ProcessError, match="살아있습니다"):
        pm.kill_external(_make_service(port=12345), evaluation=evaluation, poll_interval=1)
    assert [sig for _, sig in sent] == [module.signal.SIGINT]


def test_kill_external_inaccessible_after_signal_is_inconclusive(pm, monkeypatch, simulated_external):
    module, state, sent, evaluation = simulated_external

    def send(pid, sig):
        sent.append((pid, sig))
        state["denied"] = True

    monkeypatch.setattr(module.os, "kill", send)
    with pytest.raises(ProcessError, match="생존 여부를 확인할 수 없습니다"):
        pm.kill_external(_make_service(port=12345), evaluation=evaluation)
    assert len(sent) == 1


def test_kill_external_succeeds_when_process_exits_after_listener_closes(pm, monkeypatch, simulated_external):
    module, state, sent, evaluation = simulated_external

    def send(pid, sig):
        sent.append((pid, sig))
        state["holder"] = False

    def sleep(delay):
        state["time"] += delay
        state["alive"] = False

    monkeypatch.setattr(module.os, "kill", send)
    monkeypatch.setattr(module.time, "sleep", sleep)
    result = pm.kill_external(_make_service(port=12345), evaluation=evaluation)
    assert result["signal"] == "SIGINT"
    assert len(sent) == 1


def test_kill_external_rejects_forbidden_pid(pm, simulated_external):
    _, _, sent, evaluation = simulated_external
    with pytest.raises(ProcessError, match="종료할 수 없습니다"):
        pm.kill_external(_make_service(port=12345), evaluation=evaluation, forbidden_pids={12345})
    assert sent == []


@pytest.mark.parametrize("executable,cwd", [("/other/python", "/tmp"), ("/usr/bin/python", "/other")])
def test_destructive_identity_rejects_wrong_executable_or_cwd(monkeypatch, executable, cwd):
    import process_manager as module

    class FakeProcess:
        def __init__(self, _pid):
            pass

        def cmdline(self):
            return ["/other/python", "x.py"]

        def cwd(self):
            return cwd

        def exe(self):
            return executable

    monkeypatch.setattr(module.psutil, "Process", FakeProcess)
    service = replace(_make_service(port=12345), command=("/usr/bin/python", "x.py"))
    with pytest.raises(ProcessError, match="실행 파일 또는 cwd"):
        module._verify_destructive_identity(service, 12345, 10.0)


@pytest.mark.parametrize('delta', [0.5, 0.005, 0.0001])
def test_external_signal_identity_rejects_nearby_birth(delta):
    from unittest.mock import Mock, patch
    from process_manager import _external_process_alive, _pid_identity_is_current, RuntimeState
    proc = Mock()
    proc.create_time.return_value = 100.0 + delta
    proc.is_running.return_value = True
    proc.status.return_value = psutil.STATUS_SLEEPING
    with patch('process_manager.psutil.Process', return_value=proc):
        assert _external_process_alive(99, 100.0) is False
        assert _pid_identity_is_current(RuntimeState(pid=99, create_time=100.0)) is False
