"""process_manager 통합 테스트.

실제 Python 자식 프로세스를 띄워 시작/중지/재시작 사이클을 검증한다.
SIGINT를 안전하게 처리하고 stdout/stderr가 로그 파일에 남는지도 확인한다.
"""
from __future__ import annotations

import os
import signal
import time
from dataclasses import replace
from pathlib import Path

import psutil
import pytest

from config_loader import (
    HealthConfig,
    HttpsConfig,
    LifecycleConfig,
    LogConfig,
    ServiceConfig,
    StopStrategy,
)
from log_manager import LogManager
from process_manager import ProcessError, ProcessManager, RuntimeState
import process_manager as process_manager_module


def _make_service(tmp_path: Path, *, sid: str = "echoer", lifetime: float = 5.0) -> ServiceConfig:
    """짧은 echo loop 자식 프로세스를 띄우는 ServiceConfig를 만든다."""

    script = tmp_path / "child.py"
    script.write_text(
        "import signal, sys, time\n"
        "running = True\n"
        "def handler(signum, frame):\n"
        "    global running\n"
        "    running = False\n"
        "signal.signal(signal.SIGINT, handler)\n"
        "signal.signal(signal.SIGTERM, handler)\n"
        "i = 0\n"
        f"deadline = time.time() + {lifetime}\n"
        "while running and time.time() < deadline:\n"
        "    print(f'tick {i}', flush=True)\n"
        "    i += 1\n"
        "    time.sleep(0.1)\n"
        "print('bye', flush=True)\n",
        encoding="utf-8",
    )

    return ServiceConfig(
        id=sid,
        name=sid,
        description="",
        cwd=str(tmp_path),
        entry_file="child.py",
        command=("python", "-u", "child.py"),
        env={},
        port=None,
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


def _stop_strategy(timeout: float = 3.0) -> StopStrategy:
    return StopStrategy(
        signal="SIGINT",
        timeout_seconds=timeout,
        confirm_required=False,
        fallback=("SIGTERM", "SIGKILL"),
    )


def test_start_then_stop(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    runtime_dir = tmp_path / "runtime"
    lm = LogManager(log_dir)
    pm = ProcessManager(runtime_dir, lm)
    service = _make_service(tmp_path)

    state = pm.start(service)
    assert state.pid is not None
    assert pm.is_alive(service.id)

    # 자식이 실제로 출력하는지 확인
    time.sleep(0.5)
    log = (log_dir / "echoer.log").read_text(encoding="utf-8")
    assert "tick 0" in log

    pm.stop(service, _stop_strategy())
    assert not pm.is_alive(service.id)
    state_after = pm.get_state(service.id)
    assert state_after.pid is None
    assert state_after.last_exit_time is not None
    # SIGINT로 정상 종료했으면 exit code 0이거나 None(추적 못함)이거나 모두 허용.


def test_start_injects_https_environment(tmp_path: Path) -> None:
    script = tmp_path / "https_env.py"
    script.write_text(
        "import os, time\n"
        "print('enabled=' + os.getenv('DEV_HTTPS', ''), flush=True)\n"
        "print('cert=' + os.getenv('DEV_CERT_FILE', ''), flush=True)\n"
        "print('key=' + os.getenv('DEV_KEY_FILE', ''), flush=True)\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    service = _make_service(tmp_path, sid="httpsenv")
    service = ServiceConfig(
        id=service.id,
        name=service.name,
        description=service.description,
        cwd=service.cwd,
        entry_file="https_env.py",
        command=("python", "-u", "https_env.py"),
        env={},
        port=None,
        port_env_name=None,
        open_url=None,
        health=service.health,
        log=service.log,
        lifecycle=service.lifecycle,
        actions=service.actions,
        https=HttpsConfig(
            enabled=True,
            cert_file=".certs/dev.crt",
            key_file=".certs/dev.key",
            enabled_env_name="DEV_HTTPS",
            cert_file_env_name="DEV_CERT_FILE",
            key_file_env_name="DEV_KEY_FILE",
        ),
    )
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)

    pm.start(service)
    try:
        time.sleep(0.5)
        log = (tmp_path / "logs" / "httpsenv.log").read_text(encoding="utf-8")
        assert "enabled=1" in log
        assert f"cert={tmp_path}/.certs/dev.crt" in log
        assert f"key={tmp_path}/.certs/dev.key" in log
    finally:
        pm.stop(service, _stop_strategy())


def test_start_when_already_running(tmp_path: Path) -> None:
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    service = _make_service(tmp_path)

    pm.start(service)
    try:
        with pytest.raises(ProcessError, match="이미 실행 중"):
            pm.start(service)
    finally:
        pm.stop(service, _stop_strategy())


def test_stop_when_already_dead_is_idempotent(tmp_path: Path) -> None:
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    service = _make_service(tmp_path)

    pm.start(service)
    pm.stop(service, _stop_strategy())
    # 두 번째 stop도 예외 없이 통과
    pm.stop(service, _stop_strategy())
    assert not pm.is_alive(service.id)


def test_retired_service_blocks_stale_start_after_same_id_recreated(tmp_path: Path) -> None:
    """삭제 완료 뒤 old ServiceConfig는 같은 ID가 재등록돼도 start할 수 없다."""
    pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
    old = _make_service(tmp_path, sid="recreated")
    pm.activate_service(old)
    pm._states[old.id] = RuntimeState(last_exit_time=1.0)
    pm._save(old.id, pm._states[old.id])
    pm.forget_service(old.id)

    newer = replace(old, name="new definition")
    pm.activate_service(newer)
    assert pm.get_state(old.id).pid is None
    assert not (tmp_path / "runtime" / "recreated.json").exists()
    with pytest.raises(ProcessError, match="최신 설정"):
        pm.start(old)


def test_reconcile_service_definitions_updates_active_definition(tmp_path: Path) -> None:
    """수동 reload가 새 ServiceConfig를 ProcessManager에도 반영한다."""
    pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
    old = _make_service(tmp_path, sid="reloadable")
    new = replace(old, name="changed")
    pm.activate_service(old)
    pm.reconcile_service_definitions((new,))
    assert pm._active_services[old.id] == new


def test_reconcile_removal_serializes_with_start(tmp_path: Path, monkeypatch) -> None:
    """reload 제거가 service lock을 잡은 뒤 대기 start는 retire 뒤에만 실행돼 거부된다."""
    import threading

    pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
    service = _make_service(tmp_path, sid="removed")
    pm.activate_service(service)
    entered = threading.Event()
    resume = threading.Event()
    original_alive = pm._is_alive

    def pause_alive(state):
        if state.pid is None:
            entered.set()
            assert resume.wait(timeout=5)
        return original_alive(state)

    result: list[Exception] = []
    with monkeypatch.context() as m:
        m.setattr(pm, "_is_alive", pause_alive)
        reloader = threading.Thread(target=lambda: pm.reconcile_service_definitions(()))
        reloader.start()
        assert entered.wait(timeout=5)

        def start_old():
            try:
                pm.start(service)
            except Exception as exc:  # noqa: BLE001
                result.append(exc)

        starter = threading.Thread(target=start_old)
        starter.start()
        resume.set()
        reloader.join(timeout=5)
        starter.join(timeout=5)

    assert not reloader.is_alive()
    assert not starter.is_alive()
    assert len(result) == 1
    assert isinstance(result[0], ProcessError)
    assert pm.get_state(service.id).pid is None


def test_restart_recreates_pid(tmp_path: Path) -> None:
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    service = _make_service(tmp_path)

    state1 = pm.start(service)
    state2 = pm.restart(service, _stop_strategy())
    try:
        assert state1.pid != state2.pid
        assert pm.is_alive(service.id)
    finally:
        pm.stop(service, _stop_strategy())


def test_runtime_state_persisted(tmp_path: Path) -> None:
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    service = _make_service(tmp_path)

    pm.start(service)
    runtime_file = tmp_path / "runtime" / "echoer.json"
    assert runtime_file.is_file()
    data = runtime_file.read_text(encoding="utf-8")
    assert '"pid"' in data
    pm.stop(service, _stop_strategy())

    # 새 ProcessManager는 디스크 상태를 복구해야 한다 (이번엔 stopped 상태)
    pm2 = ProcessManager(tmp_path / "runtime", lm)
    assert pm2.get_state("echoer").pid is None
    assert pm2.get_state("echoer").last_exit_time is not None


def test_kills_unresponsive_child_with_fallback(tmp_path: Path) -> None:
    """SIGINT를 무시하는 자식도 SIGTERM/SIGKILL fallback으로 종료되는지."""
    script = tmp_path / "stubborn.py"
    script.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "while True:\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    service = ServiceConfig(
        id="stubborn",
        name="stubborn",
        description="",
        cwd=str(tmp_path),
        entry_file="stubborn.py",
        command=("python", "-u", "stubborn.py"),
        env={},
        port=None,
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

    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    state = pm.start(service)
    pid = state.pid
    assert pid is not None

    # 짧은 timeout으로 fallback이 빠르게 일어나도록.
    pm.stop(
        service,
        StopStrategy(
            signal="SIGINT",
            timeout_seconds=1.0,
            confirm_required=False,
            fallback=("SIGTERM", "SIGKILL"),
        ),
    )

    # 살아있다면 안전망으로 강제 종료
    if psutil.pid_exists(pid):
        # psutil 입장에선 zombie일 수 있어 잠깐 대기
        time.sleep(0.2)
    assert not pm.is_alive(service.id)


def test_stop_waits_for_remaining_process_group_children(tmp_path: Path) -> None:
    """리더가 먼저 끝나도 같은 group의 자식이 남으면 SIGKILL까지 진행한다."""
    script = tmp_path / "parent_with_stubborn_child.py"
    script.write_text(
        "import signal, subprocess, sys, time\n"
        "child_code = (\n"
        "    'import signal, time\\n'\n"
        "    'signal.signal(signal.SIGINT, signal.SIG_IGN)\\n'\n"
        "    'signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n'\n"
        "    'while True: time.sleep(0.1)\\n'\n"
        ")\n"
        "child = subprocess.Popen([sys.executable, '-u', '-c', child_code])\n"
        "print(child.pid, flush=True)\n"
        "def stop_parent(signum, frame):\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGINT, stop_parent)\n"
        "signal.signal(signal.SIGTERM, stop_parent)\n"
        "while True: time.sleep(0.1)\n",
        encoding="utf-8",
    )
    service = ServiceConfig(
        id="group_children",
        name="group_children",
        description="",
        cwd=str(tmp_path),
        entry_file=script.name,
        command=("python", "-u", script.name),
        env={},
        port=None,
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
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    parent = pm.start(service)
    assert parent.pid is not None and parent.pgid is not None

    child_pid: int | None = None
    for _ in range(30):
        lines = (tmp_path / "logs" / "group_children.log").read_text(encoding="utf-8").splitlines()
        if lines:
            child_pid = int(lines[0])
            break
        time.sleep(0.05)
    assert child_pid is not None
    assert os.getpgid(child_pid) == parent.pgid

    try:
        pm.stop(
            service,
            StopStrategy(
                signal="SIGINT",
                timeout_seconds=0.3,
                confirm_required=False,
                fallback=("SIGTERM", "SIGKILL"),
            ),
        )
        assert pm.get_state(service.id).pid is None
        assert not psutil.pid_exists(child_pid)
    finally:
        if psutil.pid_exists(child_pid):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_stale_pid_not_alive(tmp_path: Path) -> None:
    """create_time이 일치하지 않으면 살아있어도 다른 프로세스라 판단."""
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    service = _make_service(tmp_path)

    state = pm.start(service)
    try:
        # 관찰용 복사본의 create_time만 바꾼다. PM이 보관한 runtime identity는
        # cleanup/stop에서 계속 실제 프로세스 identity를 사용해야 한다.
        stale_observation = replace(state, create_time=(state.create_time or 0) - 1000)
        assert not pm._is_alive(stale_observation)  # type: ignore[attr-defined]
    finally:
        pm.stop(service, _stop_strategy())



def test_rejects_start_when_port_is_taken(tmp_path: Path) -> None:
    """다른 프로세스가 service.port를 잡고 있으면 명확한 에러로 거부."""
    import socket

    # 임의 포트 점유
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    occupied_port = sock.getsockname()[1]

    try:
        script = tmp_path / "child.py"
        script.write_text(
            "import time\nwhile True: time.sleep(0.1)\n",
            encoding="utf-8",
        )
        service = ServiceConfig(
            id="port_collide",
            name="port_collide",
            description="",
            cwd=str(tmp_path),
            entry_file="child.py",
            command=("python", "-u", "child.py"),
            env={},
            port=occupied_port,
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

        lm = LogManager(tmp_path / "logs")
        pm = ProcessManager(tmp_path / "runtime", lm)

        with pytest.raises(ProcessError, match="이미 사용 중"):
            pm.start(service)
    finally:
        sock.close()


def test_find_port_holder_ignores_bind_only_false_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    """LISTEN 프로세스가 없으면 bind 실패성 상태만으로 holder를 만들지 않는다."""
    import net_utils

    monkeypatch.setattr(net_utils, "holder_via_psutil", lambda _port: None)
    monkeypatch.setattr(net_utils, "holder_via_lsof", lambda _port: None)
    monkeypatch.setattr(net_utils, "port_accepts_connections", lambda _port: False)

    assert net_utils.find_port_holder(9033) is None


def test_find_port_holder_keeps_unknown_listener_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """psutil/lsof가 놓쳐도 TCP connect가 되면 unknown holder로 보수적으로 막는다."""
    import net_utils

    monkeypatch.setattr(net_utils, "holder_via_psutil", lambda _port: None)
    monkeypatch.setattr(net_utils, "holder_via_lsof", lambda _port: None)
    monkeypatch.setattr(net_utils, "port_accepts_connections", lambda _port: True)

    assert net_utils.find_port_holder(9033) == {
        "pid": None,
        "name": "unknown",
        "addr": "",
    }


def test_find_port_holder_uses_lsof_when_psutil_has_no_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """psutil이 listener는 보지만 PID를 못 주면 lsof의 실제 PID를 우선한다."""
    import net_utils

    monkeypatch.setattr(net_utils, "holder_via_psutil", lambda _port: {"pid": None, "name": "", "addr": ""})
    monkeypatch.setattr(net_utils, "holder_via_lsof", lambda _port: {"pid": 1234, "name": "server", "addr": ""})
    assert net_utils.find_port_holder(9033)["pid"] == 1234



def test_child_survives_controller_restart_via_fd_redirect(tmp_path: Path) -> None:
    """자식 stdout/stderr를 fd로 직접 redirect하면 컨트롤 서버가 사라져도 SIGPIPE 안 맞는다.

    v1.2.3 회귀 보호용. 컨트롤 서버 재시작을 흉내내기 위해 ProcessManager 인스턴스를
    버리고 다른 인스턴스로 같은 runtime 디렉터리를 다시 로드한다. 그 사이에 자식은
    계속 돌면서 stdout에 써야 한다.
    """
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    service = _make_service(tmp_path, lifetime=3.0)

    state = pm.start(service)
    pid = state.pid
    assert pid is not None

    # 첫 번째 매니저를 그냥 버린다 (컨트롤 서버 죽음을 흉내).
    # 자식은 fd로 직접 파일에 쓰고 있으므로 부모가 사라져도 영향 없다.
    pm.shutdown()
    del pm

    # 자식이 계속 살아서 stdout에 쓰고 있는지 잠시 관찰.
    time.sleep(0.6)
    assert psutil.pid_exists(pid)

    # 새 ProcessManager가 디스크 상태를 복구해도 동일 자식이 보임.
    pm2 = ProcessManager(tmp_path / "runtime", lm)
    state2 = pm2.get_state(service.id)
    assert state2.pid == pid

    # 첫 번째 stop은 새 매니저가 처리. SIGINT로 정리.
    pm2.stop(service, _stop_strategy())
    assert not pm2.is_alive(service.id)

    # 자식이 죽기 전까지 쌓인 줄이 .log에 남아있어야 한다.
    log_path = tmp_path / "logs" / "echoer.log"
    log = log_path.read_text(encoding="utf-8")
    assert "tick 0" in log
