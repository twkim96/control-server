"""v1.2.5 회귀 테스트.

핵심 보장:
1. `_is_alive`가 일과성 OS 오류에 alive로 응답
2. `_is_alive`가 create_time 짧은 mismatch + 재시도로 일치 → alive
3. `reap_if_dead`가 두 번 false일 때만 정리
4. cmdline prefix 매치 정책 (basename 차이 허용 X, 절대경로 차이 허용 O)
5. try_adopt 성공/실패 케이스
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

from config_loader import (
    HealthConfig,
    LifecycleConfig,
    LogConfig,
    ServiceConfig,
)
from log_manager import LogManager
from process_manager import (
    AdoptDiagnostics,
    AdoptEvaluation,
    ProcessManager,
    RuntimeState,
    _AdoptCandidate,
    _cmdline_exact_match,
    _create_time_matches,
    _service_cmdline_match,
)


def _make_service(*, sid: str = "svc", port: int | None = None,
                  unmanaged_policy: str = "manage") -> ServiceConfig:
    return ServiceConfig(
        id=sid,
        name=sid,
        description="",
        cwd="/tmp",
        entry_file="app.py",
        command=("python3", "-u", "app.py"),
        env={},
        port=port,
        port_env_name=None,
        open_url=None,
        health=HealthConfig(
            enabled=True, type="http",
            url=f"http://127.0.0.1:{port}/health" if port else None,
            timeout_seconds=2.0, verify_ssl=True,
        ),
        log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
        lifecycle=LifecycleConfig(
            mode="manual",
            autostart=False,
            stop_visibility="primary",
            restart_visibility="primary",
            unmanaged_policy=unmanaged_policy,
        ),
        actions=(),
    )


# ----------------------------------------------------------------------
# 1. _is_alive 보수화
# ----------------------------------------------------------------------


def test_is_alive_returns_true_on_access_denied(tmp_path: Path) -> None:
    """psutil.AccessDenied가 나면 alive로 간주 (일과성 오류 → 잘못 정리 방지)."""
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    state = RuntimeState(pid=99999, pgid=99999, create_time=1234.0, start_time=time.time())

    with patch("process_manager.psutil.Process") as mock_proc_cls:
        mock_proc_cls.side_effect = psutil.AccessDenied(99999)
        assert pm._is_alive(state) is True


def test_is_alive_returns_true_on_oserror(tmp_path: Path) -> None:
    """일반 OSError도 alive로 간주."""
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    state = RuntimeState(pid=99999, pgid=99999, create_time=1234.0, start_time=time.time())

    with patch("process_manager.psutil.Process") as mock_proc_cls:
        mock_proc_cls.side_effect = OSError("transient")
        assert pm._is_alive(state) is True


def test_is_alive_returns_false_only_on_no_such_process(tmp_path: Path) -> None:
    """진짜 NoSuchProcess면 dead."""
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    state = RuntimeState(pid=99999, pgid=99999, create_time=1234.0, start_time=time.time())

    with patch("process_manager.psutil.Process") as mock_proc_cls:
        mock_proc_cls.side_effect = psutil.NoSuchProcess(99999)
        assert pm._is_alive(state) is False


def test_is_alive_create_time_mismatch_with_retry(tmp_path: Path) -> None:
    """첫 호출에서 create_time이 mismatch지만 재시도에선 일치 → alive."""
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    state = RuntimeState(pid=99999, pgid=99999, create_time=100.0, start_time=time.time())

    class FakeProc:
        def __init__(self, ct: float) -> None:
            self._ct = ct

        def create_time(self) -> float:
            return self._ct

        def is_running(self) -> bool:
            return True

        def status(self) -> str:
            return psutil.STATUS_RUNNING

    # 첫 호출: ct=200 (mismatch). 두 번째 호출: ct=100 (match).
    procs_iter = iter([FakeProc(200.0), FakeProc(100.0)])

    with patch("process_manager.psutil.Process", side_effect=lambda pid: next(procs_iter)):
        # 짧은 sleep을 우회하기 위해 time.sleep도 패치
        with patch("process_manager.time.sleep"):
            assert pm._is_alive(state) is True


# ----------------------------------------------------------------------
# 2. reap_if_dead retry
# ----------------------------------------------------------------------


def test_reap_if_dead_keeps_state_when_first_false_second_true(tmp_path: Path) -> None:
    """첫 _is_alive=False, 두 번째 True면 정리 안 함 (일과성 false 방지)."""
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    state = RuntimeState(pid=12345, pgid=12345, create_time=time.time(), start_time=time.time())
    pm._states["svc"] = state

    calls = {"n": 0}

    def fake_is_alive(s: RuntimeState) -> bool:
        calls["n"] += 1
        return calls["n"] >= 2  # 첫 호출 False, 두 번째 True

    with patch.object(pm, "_is_alive", side_effect=fake_is_alive):
        with patch("process_manager.time.sleep"):
            result = pm.reap_if_dead("svc")
    assert result is False
    # state는 그대로 남아 있어야 함
    assert pm._states["svc"].pid == 12345


def test_reap_if_dead_cleans_when_both_false(tmp_path: Path) -> None:
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    state = RuntimeState(pid=12345, pgid=12345, create_time=time.time(), start_time=time.time())
    pm._states["svc"] = state

    with patch.object(pm, "_is_alive", return_value=False):
        with patch("process_manager.time.sleep"):
            result = pm.reap_if_dead("svc")
    assert result is True
    assert pm._states["svc"].pid is None


def test_inspect_dead_cleanup_cas_preserves_newer_state_and_runtime(tmp_path: Path) -> None:
    """오래된 dead cleanup은 새 start state/Popen/runtime을 지우지 않는다.

    inspect가 구 PID를 dead로 판정한 뒤 conditional commit 직전에 멈춘다. 그 사이
    새 start를 흉내 낸 state/Popen/runtime을 저장한 뒤 재개하면 CAS가 실패해야 한다.
    """
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    old = RuntimeState(pid=111, pgid=111, create_time=10.0, start_time=10.0)
    pm._states["svc"] = old
    pm._save("svc", old)

    entered = threading.Event()
    resume = threading.Event()
    result: list[tuple[RuntimeState, bool]] = []
    original_commit = pm._mark_stopped_if_current

    def pause_before_commit(*args, **kwargs):
        entered.set()
        assert resume.wait(timeout=5)
        return original_commit(*args, **kwargs)

    newer = RuntimeState(pid=222, pgid=222, create_time=20.0, start_time=20.0)
    newer_popen = object()
    with patch.object(pm, "_is_alive", return_value=False), patch.object(
        pm, "_mark_stopped_if_current", side_effect=pause_before_commit
    ), patch.object(lm, "stop_capture") as stop_capture:
        worker = threading.Thread(target=lambda: result.append(pm.inspect_state("svc")))
        worker.start()
        assert entered.wait(timeout=5)
        with pm._lock:
            pm._states["svc"] = newer
            pm._popens["svc"] = newer_popen  # type: ignore[assignment]
            pm._save("svc", newer)
        resume.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert result == [(newer, True)]
    assert pm._states["svc"] is newer
    assert pm._popens["svc"] is newer_popen
    persisted = RuntimeState.from_json((tmp_path / "runtime" / "svc.json").read_text())
    assert persisted.pid == 222
    assert persisted.create_time == 20.0
    stop_capture.assert_not_called()


# ----------------------------------------------------------------------
# 3. cmdline prefix 매치 헬퍼
# ----------------------------------------------------------------------


def test_cmdline_prefix_match_exact() -> None:
    assert _cmdline_exact_match(["python3", "-u", "app.py"], ["python3", "-u", "app.py"]) is True


def test_cmdline_prefix_match_absolute_basename_same() -> None:
    """절대경로 대 짧은 이름: basename이 같으면 일치."""
    assert _cmdline_exact_match(
        ["python3", "-u", "app.py"],
        ["/opt/anaconda3/bin/python3", "-u", "app.py"],
    ) is True


def test_cmdline_prefix_match_basename_different_rejected() -> None:
    """python vs python3는 다른 명령으로 본다."""
    assert _cmdline_exact_match(
        ["python", "-u", "app.py"],
        ["/opt/anaconda3/bin/python3", "-u", "app.py"],
    ) is False


def test_cmdline_prefix_match_args_must_match() -> None:
    assert _cmdline_exact_match(
        ["python3", "-u", "app.py"],
        ["python3", "app.py"],
    ) is False


def test_service_adopt_prefix_is_explicit_opt_in() -> None:
    base = replace(
        _make_service(),
        adopt_command=("server", "start"),
        adopt_match="exact",
    )
    actual = ["server", "start", "--port", "10100"]
    assert _service_cmdline_match(base, actual) is False
    assert _service_cmdline_match(replace(base, adopt_match="prefix"), actual) is True
    assert _service_cmdline_match(replace(base, adopt_match="prefix"), ["server", "other"]) is False
    assert _cmdline_exact_match(
        ["python3", "-u", "app.py"],
        ["python3", "-u", "different.py"],
    ) is False


def test_cmdline_prefix_match_empty() -> None:
    assert _cmdline_exact_match([], ["python3"]) is False
    assert _cmdline_exact_match(["python3"], []) is False


def test_service_cmdline_match_infers_tailscale_https_exec_command() -> None:
    service = replace(
        _make_service(),
        command=(
            "/bin/bash",
            "/tmp/with_tailscale_https.sh",
            "--",
            "/opt/python/bin/python3",
            "-u",
            "app.py",
        ),
        adopt_command=None,
    )

    assert _service_cmdline_match(
        service,
        ["/opt/python/bin/python3", "-u", "app.py"],
    ) is True


def test_service_cmdline_match_does_not_infer_unknown_wrapper() -> None:
    service = replace(
        _make_service(),
        command=("/bin/bash", "/tmp/other-wrapper.sh", "--", "python3", "-u", "app.py"),
        adopt_command=None,
    )

    assert _service_cmdline_match(service, ["python3", "-u", "app.py"]) is False


# ----------------------------------------------------------------------
# 4. _create_time_matches 헬퍼
# ----------------------------------------------------------------------


def test_create_time_matches_within_tolerance() -> None:
    class FakeProc:
        def create_time(self) -> float:
            return 100.5

    assert _create_time_matches(FakeProc(), 100.0) is True   # 차이 0.5 → 일치
    assert _create_time_matches(FakeProc(), 102.0) is False  # 차이 1.5 → 불일치


def test_create_time_matches_returns_true_on_error() -> None:
    """create_time 호출이 일과성 예외를 던지면 일치한다고 가정."""
    class FakeProc:
        def create_time(self) -> float:
            raise psutil.AccessDenied(0)

    assert _create_time_matches(FakeProc(), 100.0) is True


# ----------------------------------------------------------------------
# 5. try_adopt 통합 (실제 자식 spawn)
# ----------------------------------------------------------------------


def _spawn_listener(port: int, *, cwd: str | None = None) -> subprocess.Popen:
    """진짜 listener 프로세스를 띄운다. v1.2.6: GET 요청에 200으로 응답하는 mini HTTP.

    cwd를 인자로 받아 입양 cwd 검증을 통과시킬 수 있도록 한다.
    """
    code = f"""
import signal, socket, time
running = True
def h(s, f):
    global running
    running = False
signal.signal(signal.SIGINT, h)
signal.signal(signal.SIGTERM, h)
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('127.0.0.1', {port}))
sock.listen(1)
sock.settimeout(0.1)
body = b'{{"ok": true}}'
resp = (b'HTTP/1.1 200 OK\\r\\n'
        b'Content-Type: application/json\\r\\n'
        b'Content-Length: ' + str(len(body)).encode() + b'\\r\\n'
        b'Connection: close\\r\\n\\r\\n' + body)
while running:
    try:
        conn, _ = sock.accept()
        try:
            conn.settimeout(0.2)
            try:
                conn.recv(4096)
            except Exception:
                pass
            conn.sendall(resp)
        finally:
            conn.close()
    except socket.timeout:
        pass
sock.close()
"""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", code],
        cwd=cwd,
    )
    # LISTEN 시작 대기
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
    raise RuntimeError("listener failed to start")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_try_adopt_succeeds_when_cmdline_matches(tmp_path: Path) -> None:
    """외부에 떠있는 자식의 cmdline+cwd+health가 매치되면 입양 성공 (v1.2.6 정책)."""
    port = _free_port()
    cwd_for_child = str(tmp_path)
    listener = _spawn_listener(port, cwd=cwd_for_child)
    try:
        actual_cmdline = psutil.Process(listener.pid).cmdline()
        service = ServiceConfig(
            id="adopt_target",
            name="adopt",
            description="",
            cwd=cwd_for_child,  # 자식이 실제로 그 cwd에서 떠야 함
            entry_file="-",
            command=tuple(actual_cmdline),
            env={},
            port=port,
            port_env_name=None,
            open_url=None,
            health=HealthConfig(
                enabled=True, type="http",
                url=f"http://127.0.0.1:{port}/health",
                timeout_seconds=2.0, verify_ssl=True,
            ),
            log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
            lifecycle=LifecycleConfig(
                mode="manual",
                autostart=False,
                stop_visibility="primary",
                restart_visibility="primary",
                unmanaged_policy="manage",
            ),
            actions=(),
        )
        lm = LogManager(tmp_path / "logs")
        pm = ProcessManager(tmp_path / "runtime", lm)

        adopted = pm.try_adopt(service)
        assert adopted is not None
        assert adopted.pid == listener.pid
        assert adopted.last_action == "adopt"
        assert adopted.create_time is not None
        assert adopted.adopted is True
        # 디스크에도 저장됐어야 함
        state = pm.get_state("adopt_target")
        assert state.pid == listener.pid
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def test_try_adopt_preserves_prior_exit_history_and_extra(tmp_path: Path) -> None:
    """입양 시 직전 종료 이력/extra를 보존하고 start_time을 실제 create_time으로 잡는다."""
    port = _free_port()
    cwd_for_child = str(tmp_path)
    listener = _spawn_listener(port, cwd=cwd_for_child)
    try:
        actual_cmdline = psutil.Process(listener.pid).cmdline()
        service = ServiceConfig(
            id="adopt_hist",
            name="adopt",
            description="",
            cwd=cwd_for_child,
            entry_file="-",
            command=tuple(actual_cmdline),
            env={},
            port=port,
            port_env_name=None,
            open_url=None,
            health=HealthConfig(
                enabled=True, type="http",
                url=f"http://127.0.0.1:{port}/health",
                timeout_seconds=2.0, verify_ssl=True,
            ),
            log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
            lifecycle=LifecycleConfig(
                mode="manual",
                autostart=False,
                stop_visibility="primary",
                restart_visibility="primary",
                unmanaged_policy="manage",
            ),
            actions=(),
        )
        lm = LogManager(tmp_path / "logs")
        pm = ProcessManager(tmp_path / "runtime", lm)

        # 입양 직전 상태: 비정상 종료 이력 + extra 메타데이터를 심어둔다.
        pm._states["adopt_hist"] = RuntimeState(
            pid=None,
            last_exit_code=-9,
            last_exit_time=1000.0,
            last_action="start",
            extra={"note": "keep-me"},
        )

        adopted = pm.try_adopt(service)
        assert adopted is not None
        assert adopted.adopted is True
        # 이력 보존
        assert adopted.last_exit_code == -9
        assert adopted.last_exit_time == 1000.0
        assert adopted.extra == {"note": "keep-me"}
        # start_time은 입양 시각이 아니라 프로세스의 실제 create_time
        assert adopted.create_time is not None
        assert adopted.start_time == adopted.create_time
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def test_try_adopt_succeeds_when_adopt_command_matches(tmp_path: Path) -> None:
    """래퍼 시작 명령과 달라도 명시된 최종 명령이면 입양한다."""
    port = _free_port()
    cwd_for_child = str(tmp_path)
    listener = _spawn_listener(port, cwd=cwd_for_child)
    try:
        actual_cmdline = tuple(psutil.Process(listener.pid).cmdline())
        service = ServiceConfig(
            id="wrapped",
            name="wrapped",
            description="",
            cwd=cwd_for_child,
            entry_file="run.sh",
            command=("/bin/bash", "run.sh"),
            adopt_command=actual_cmdline,
            env={},
            port=port,
            port_env_name=None,
            open_url=None,
            health=HealthConfig(
                enabled=True,
                type="http",
                url=f"http://127.0.0.1:{port}/health",
                timeout_seconds=2.0,
                verify_ssl=True,
            ),
            log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
            lifecycle=LifecycleConfig(
                mode="manual",
                autostart=False,
                stop_visibility="primary",
                restart_visibility="primary",
                unmanaged_policy="manage",
            ),
            actions=(),
        )
        pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))

        adopted = pm.try_adopt(service)

        assert adopted is not None
        assert adopted.pid == listener.pid
        assert adopted.adopted is True
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def test_try_adopt_rejects_when_cwd_mismatch(tmp_path: Path) -> None:
    """cwd가 다르면 입양 거부 (v1.2.6)."""
    port = _free_port()
    actual_cwd = tmp_path / "actual"
    actual_cwd.mkdir()
    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    listener = _spawn_listener(port, cwd=str(actual_cwd))
    try:
        actual_cmdline = psutil.Process(listener.pid).cmdline()
        service = ServiceConfig(
            id="cwd_mismatch",
            name="cwd_mismatch",
            description="",
            cwd=str(other_cwd),  # 자식의 실제 cwd와 다름
            entry_file="-",
            command=tuple(actual_cmdline),
            env={},
            port=port,
            port_env_name=None,
            open_url=None,
            health=HealthConfig(
                enabled=True, type="http",
                url=f"http://127.0.0.1:{port}/health",
                timeout_seconds=2.0, verify_ssl=True,
            ),
            log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
            lifecycle=LifecycleConfig(
                mode="manual",
                autostart=False,
                stop_visibility="primary",
                restart_visibility="primary",
                unmanaged_policy="manage",
            ),
            actions=(),
        )
        lm = LogManager(tmp_path / "logs")
        pm = ProcessManager(tmp_path / "runtime", lm)
        assert pm.try_adopt(service) is None
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def test_try_adopt_rejects_when_health_unreachable(tmp_path: Path) -> None:
    """health URL이 응답 없으면 입양 거부 (v1.2.6)."""
    port = _free_port()
    cwd_for_child = str(tmp_path)
    listener = _spawn_listener(port, cwd=cwd_for_child)
    try:
        actual_cmdline = psutil.Process(listener.pid).cmdline()
        # health URL을 다른 포트로 향하게 → 응답 없음
        bad_health_port = _free_port()
        service = ServiceConfig(
            id="health_unreach",
            name="health_unreach",
            description="",
            cwd=cwd_for_child,
            entry_file="-",
            command=tuple(actual_cmdline),
            env={},
            port=port,
            port_env_name=None,
            open_url=None,
            health=HealthConfig(
                enabled=True, type="http",
                url=f"http://127.0.0.1:{bad_health_port}/health",
                timeout_seconds=1.0, verify_ssl=True,
            ),
            log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
            lifecycle=LifecycleConfig(
                mode="manual",
                autostart=False,
                stop_visibility="primary",
                restart_visibility="primary",
                unmanaged_policy="manage",
            ),
            actions=(),
        )
        lm = LogManager(tmp_path / "logs")
        pm = ProcessManager(tmp_path / "runtime", lm)
        assert pm.try_adopt(service) is None
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def test_try_adopt_skips_when_cmdline_mismatches(tmp_path: Path) -> None:
    port = _free_port()
    listener = _spawn_listener(port, cwd=str(tmp_path))
    try:
        service = _make_service(sid="mismatch", port=port)
        # service.command는 ["python3", "-u", "app.py"]로 자식의 실제 cmdline과 다름.
        lm = LogManager(tmp_path / "logs")
        pm = ProcessManager(tmp_path / "runtime", lm)

        adopted = pm.try_adopt(service)
        assert adopted is None
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def test_try_adopt_skips_when_policy_is_status_only(tmp_path: Path) -> None:
    port = _free_port()
    cwd_for_child = str(tmp_path)
    listener = _spawn_listener(port, cwd=cwd_for_child)
    try:
        actual_cmdline = psutil.Process(listener.pid).cmdline()
        service = ServiceConfig(
            id="status_only",
            name="status_only",
            description="",
            cwd=cwd_for_child,
            entry_file="-",
            command=tuple(actual_cmdline),
            env={},
            port=port,
            port_env_name=None,
            open_url=None,
            health=HealthConfig(
                enabled=True, type="http",
                url=f"http://127.0.0.1:{port}/health",
                timeout_seconds=2.0, verify_ssl=True,
            ),
            log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
            lifecycle=LifecycleConfig(
                mode="manual",
                autostart=False,
                stop_visibility="primary",
                restart_visibility="primary",
                unmanaged_policy="status_only",  # manage가 아님
            ),
            actions=(),
        )
        lm = LogManager(tmp_path / "logs")
        pm = ProcessManager(tmp_path / "runtime", lm)
        assert pm.try_adopt(service) is None
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def test_try_adopt_skips_when_no_holder(tmp_path: Path) -> None:
    """포트가 비어있으면 입양 안 함."""
    port = _free_port()
    service = _make_service(sid="empty", port=port)
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    assert pm.try_adopt(service) is None


def test_try_adopt_rejects_self_pid(tmp_path: Path) -> None:
    """포트 점유자가 컨트롤 서버 자신이면 입양 안 함."""
    import process_manager as pm_module

    own_pid = os.getpid()
    service = _make_service(sid="self", port=12345)

    def fake_find(_port):
        return {"pid": own_pid, "name": "self", "addr": ""}

    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    original = pm_module._find_port_holder
    pm_module._find_port_holder = fake_find
    try:
        assert pm.try_adopt(service) is None
    finally:
        pm_module._find_port_holder = original


def test_try_adopt_rejects_other_tracked_pid(tmp_path: Path) -> None:
    """다른 서비스가 추적 중인 PID면 입양 안 함."""
    import process_manager as pm_module

    fake_pid = 999999
    service = _make_service(sid="conflict", port=12346)

    def fake_find(_port):
        return {"pid": fake_pid, "name": "tracked", "addr": ""}

    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    pm._states["other"] = RuntimeState(pid=fake_pid, pgid=fake_pid)

    original = pm_module._find_port_holder
    pm_module._find_port_holder = fake_find
    try:
        assert pm.try_adopt(service) is None
    finally:
        pm_module._find_port_holder = original


def _successful_adopt_evaluation(pm: ProcessManager, service: ServiceConfig) -> AdoptEvaluation:
    pid = os.getpid()
    create_time = psutil.Process(pid).create_time()
    return AdoptEvaluation(
        diagnostics=AdoptDiagnostics(ok=True, reason="ok", candidate_pid=pid),
        candidate=_AdoptCandidate(pid=pid, pgid=os.getpgrp(), create_time=create_time),
        state_snapshot=pm._states.get(service.id, RuntimeState()),
    )


def test_late_adopt_commit_preserves_newer_start_state_and_popen(tmp_path: Path) -> None:
    """평가 뒤 생긴 start state는 늦은 adopt commit이 덮거나 pop하지 않는다."""
    import process_manager as pm_module

    pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
    service = _make_service(sid="race", port=23456)
    evaluation = _successful_adopt_evaluation(pm, service)
    entered = threading.Event()
    resume = threading.Event()
    original_commit = pm._commit_adopt_evaluation
    result: list[RuntimeState | None] = []

    def delayed_commit(*args, **kwargs):
        entered.set()
        assert resume.wait(timeout=5)
        return original_commit(*args, **kwargs)

    newer = RuntimeState(pid=222, pgid=222, create_time=20.0, start_time=20.0)
    newer_popen = object()
    with patch.object(pm, "_evaluate_adopt", return_value=evaluation), patch.object(
        pm, "_commit_adopt_evaluation", side_effect=delayed_commit
    ), patch.object(pm_module, "_find_port_holder", return_value={"pid": os.getpid()}):
        worker = threading.Thread(target=lambda: result.append(pm.try_adopt(service)))
        worker.start()
        assert entered.wait(timeout=5)
        with pm._lock:
            pm._states[service.id] = newer
            pm._popens[service.id] = newer_popen  # type: ignore[assignment]
            pm._save(service.id, newer)
        resume.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert result == [None]
    assert pm._states[service.id] is newer
    assert pm._popens[service.id] is newer_popen
    assert RuntimeState.from_json((tmp_path / "runtime" / "race.json").read_text()).pid == 222


def test_adopt_commit_allows_one_service_per_process_identity(tmp_path: Path) -> None:
    """같은 PID/create_time 후보는 동시에 두 서비스가 추적할 수 없다."""
    import process_manager as pm_module

    pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
    first = _make_service(sid="first", port=23457)
    second = _make_service(sid="second", port=23458)
    first_eval = _successful_adopt_evaluation(pm, first)
    second_eval = _successful_adopt_evaluation(pm, second)

    with patch.object(pm_module, "_find_port_holder", return_value={"pid": os.getpid()}):
        assert pm._commit_adopt_evaluation(first, first_eval) is not None
        assert pm._commit_adopt_evaluation(second, second_eval) is None
    assert pm.get_state("first").pid == os.getpid()
    assert pm.get_state("second").pid is None



# ----------------------------------------------------------------------
# 6. v1.2.6: stop이 입양 자식엔 PID kill, 우리 spawn 자식엔 group kill
# ----------------------------------------------------------------------


def test_can_use_group_kill_for_self_spawned() -> None:
    """우리가 spawn한 자식(adopted=False)은 group kill 사용."""
    from process_manager import _can_use_group_kill

    # pgid가 컨트롤 서버 자신과 다르고 adopted=False
    own = os.getpgrp()
    state = RuntimeState(pid=12345, pgid=12345 if own != 12345 else 99999, adopted=False)
    assert _can_use_group_kill(state) is True


def test_can_use_group_kill_rejects_self_pgrp() -> None:
    """컨트롤 서버 자신의 pgid는 절대 group kill 안 함."""
    from process_manager import _can_use_group_kill

    own = os.getpgrp()
    state = RuntimeState(pid=12345, pgid=own, adopted=False)
    assert _can_use_group_kill(state) is False


def test_can_use_group_kill_adopted_only_when_group_leader() -> None:
    """입양 자식은 자기가 group leader (pgid == pid)일 때만 group kill 허용."""
    from process_manager import _can_use_group_kill

    own = os.getpgrp()
    base = 99999 if own != 99999 else 88888
    leader = RuntimeState(pid=base, pgid=base, adopted=True)
    assert _can_use_group_kill(leader) is True

    not_leader = RuntimeState(pid=base, pgid=base + 1, adopted=True)
    assert _can_use_group_kill(not_leader) is False


def test_stop_uses_pid_kill_for_adopted(tmp_path: Path, monkeypatch) -> None:
    """입양 자식 stop 시 os.killpg가 호출되지 않고 os.kill(pid, sig)이 사용됨."""
    import process_manager as pm_module
    from config_loader import (
        HealthConfig,
        LifecycleConfig,
        LogConfig,
        ServiceConfig,
        StopStrategy,
    )

    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)

    # 입양 상태를 인위적으로 주입. PID는 진짜 자식이 필요하므로 짧은 sleep 자식 띄움.
    proc = subprocess.Popen([sys.executable, "-u", "-c", "import time; time.sleep(30)"])
    try:
        pgid = os.getpgid(proc.pid)
        own = os.getpgrp()
        # adopted=True + pgid가 leader가 아닌 상태로 주입 (group kill 차단 의도)
        injected = RuntimeState(
            pid=proc.pid,
            pgid=pgid if pgid != proc.pid else pgid + 100,  # leader 아님 흉내
            start_time=time.time(),
            create_time=psutil.Process(proc.pid).create_time(),
            adopted=True,
            last_action="adopt",
        )
        pm._states["adopt_target"] = injected

        service = ServiceConfig(
            id="adopt_target",
            name="adopt_target",
            description="",
            cwd=str(tmp_path),
            entry_file="-",
            command=("python3", "-u", "x.py"),
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
                unmanaged_policy="manage",
            ),
            actions=(),
        )
        strategy = StopStrategy(
            signal="SIGTERM",
            timeout_seconds=2.0,
            confirm_required=False,
            fallback=("SIGKILL",),
        )

        killpg_calls: list[tuple[int, int]] = []
        kill_calls: list[tuple[int, int]] = []
        original_killpg = pm_module.os.killpg
        original_kill = pm_module.os.kill

        def fake_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))
            return original_killpg(pgid, sig)

        def fake_kill(pid, sig):
            kill_calls.append((pid, sig))
            return original_kill(pid, sig)

        monkeypatch.setattr(pm_module.os, "killpg", fake_killpg)
        monkeypatch.setattr(pm_module.os, "kill", fake_kill)

        pm.stop(service, strategy)

        # killpg 호출이 한 번도 없어야 함
        assert killpg_calls == []
        # kill(pid, ...) 은 최소 한 번 호출
        assert any(c[0] == proc.pid for c in kill_calls)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_pid_stop_does_not_signal_reused_pid_on_fallback(tmp_path: Path, monkeypatch) -> None:
    """PID 단위 stop은 fallback 직전에 identity가 바뀌면 새 PID를 건드리지 않는다."""
    import process_manager as pm_module
    from config_loader import StopStrategy

    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)
    state = RuntimeState(
        pid=42424,
        pgid=42425,
        create_time=10.0,
        start_time=10.0,
        adopted=True,
    )
    pm._states["svc"] = state
    sent: list[tuple[int, int]] = []

    monkeypatch.setattr(
        pm_module,
        "_pid_identity_is_current",
        lambda _state: len(sent) == 0,
    )
    alive_results = iter([True, True, False])
    monkeypatch.setattr(pm, "_stop_target_is_alive", lambda *_args: next(alive_results))
    monkeypatch.setattr(pm, "_wait_for_exit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pm_module.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    pm.stop(
        _make_service(sid="svc"),
        StopStrategy(
            signal="SIGINT",
            timeout_seconds=0.1,
            confirm_required=False,
            fallback=("SIGTERM", "SIGKILL"),
        ),
    )

    assert sent == [(42424, signal.SIGINT)]
    assert pm.get_state("svc").pid is None


def test_stop_uses_group_kill_for_self_spawned(tmp_path: Path, monkeypatch) -> None:
    """우리가 spawn한 일반 자식 stop 시 os.killpg가 사용됨 (회귀 보호)."""
    import process_manager as pm_module
    from config_loader import (
        HealthConfig,
        LifecycleConfig,
        LogConfig,
        ServiceConfig,
        StopStrategy,
    )

    script = tmp_path / "child.py"
    script.write_text(
        "import signal, time\n"
        "def h(s, f):\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, h)\n"
        "signal.signal(signal.SIGINT, h)\n"
        "while True: time.sleep(0.1)\n",
        encoding="utf-8",
    )
    service = ServiceConfig(
        id="self_spawn",
        name="self_spawn",
        description="",
        cwd=str(tmp_path),
        entry_file="child.py",
        command=("python3", "-u", "child.py"),
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
            unmanaged_policy="manage",
        ),
        actions=(),
    )
    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)

    pm.start(service)
    time.sleep(0.3)  # 자식이 시그널 핸들러 등록할 시간

    killpg_calls: list[tuple[int, int]] = []
    original_killpg = pm_module.os.killpg

    def fake_killpg(pgid, sig):
        killpg_calls.append((pgid, sig))
        return original_killpg(pgid, sig)

    monkeypatch.setattr(pm_module.os, "killpg", fake_killpg)

    pm.stop(
        service,
        StopStrategy(
            signal="SIGTERM",
            timeout_seconds=2.0,
            confirm_required=False,
            fallback=("SIGKILL",),
        ),
    )

    # 일반 자식이면 killpg가 한 번 이상 호출돼야 함
    assert len(killpg_calls) >= 1



# ----------------------------------------------------------------------
# 7. v1.2.6: v1.2.5 → v1.2.6 runtime json 마이그레이션
# ----------------------------------------------------------------------


def test_from_json_migrates_legacy_extra_adopted() -> None:
    """v1.2.5 디스크 상태(`extra={"adopted": True}`)를 v1.2.6 정식 필드로 승격."""
    legacy = """{
      "pid": 12345,
      "pgid": 12345,
      "start_time": 1700000000.0,
      "create_time": 1700000000.0,
      "last_exit_code": null,
      "last_exit_time": null,
      "last_action": "adopt",
      "last_action_time": 1700000000.0,
      "extra": {"adopted": true, "other": "keep"}
    }"""
    state = RuntimeState.from_json(legacy)
    assert state.adopted is True
    # extra의 adopted는 제거되되 다른 키는 보존
    assert state.extra == {"other": "keep"}


def test_from_json_does_not_demote_explicit_adopted() -> None:
    """JSON에 adopted가 명시되어 있으면 그 값을 우선한다."""
    explicit = """{
      "pid": 1,
      "pgid": 1,
      "start_time": null,
      "create_time": null,
      "last_exit_code": null,
      "last_exit_time": null,
      "last_action": "start",
      "last_action_time": null,
      "adopted": false,
      "extra": {"adopted": true}
    }"""
    state = RuntimeState.from_json(explicit)
    # 정식 필드가 false면 그대로 false (마이그레이션이 덮어쓰지 않음)
    assert state.adopted is False


def test_from_json_default_adopted_false_for_normal_runtime() -> None:
    """일반 (start로 띄운) runtime json은 adopted=False."""
    normal = """{
      "pid": 99,
      "pgid": 99,
      "start_time": 1.0,
      "create_time": 1.0,
      "last_exit_code": null,
      "last_exit_time": null,
      "last_action": "start",
      "last_action_time": 1.0,
      "extra": {}
    }"""
    state = RuntimeState.from_json(normal)
    assert state.adopted is False


# ----------------------------------------------------------------------
# 8. diagnose_adopt: 입양 실패 사유 노출
# ----------------------------------------------------------------------


def test_diagnose_adopt_reports_policy_not_manage(tmp_path: Path) -> None:
    """status_only 정책은 policy_not_manage 사유를 돌려준다."""
    service = _make_service(sid="ext", port=23456, unmanaged_policy="status_only")
    pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
    diag = pm.diagnose_adopt(service)
    assert diag.ok is False
    assert diag.reason == "policy_not_manage"


def test_status_only_evaluation_keeps_holder_candidate_pid(tmp_path: Path) -> None:
    """status_only도 목록 PID와 상세 진단이 공유할 holder candidate를 보존한다."""
    import process_manager as pm_module

    service = _make_service(sid="external", port=23456, unmanaged_policy="status_only")
    pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
    with patch.object(pm_module, "_find_port_holder", return_value={"pid": 43210}):
        evaluation = pm.evaluate_adopt(service)
    assert evaluation.diagnostics.reason == "policy_not_manage"
    assert evaluation.diagnostics.candidate_pid == 43210


def test_deterministic_adopt_failure_uses_short_backoff(tmp_path: Path) -> None:
    """cmdline mismatch는 동일 살아 있는 candidate에 대해 10초간 재평가하지 않는다."""
    pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
    service = _make_service(sid="backoff", port=23456)
    pid = os.getpid()
    candidate = _AdoptCandidate(
        pid=pid,
        pgid=os.getpgrp(),
        create_time=psutil.Process(pid).create_time(),
    )
    evaluation = AdoptEvaluation(
        diagnostics=AdoptDiagnostics(ok=False, reason="cmdline_mismatch", candidate_pid=pid),
        candidate=candidate,
        state_snapshot=RuntimeState(),
    )
    with patch.object(pm, "_evaluate_adopt", return_value=evaluation) as evaluate:
        assert pm.evaluate_adopt(service).diagnostics.reason == "cmdline_mismatch"
        assert pm.evaluate_adopt(service).diagnostics.reason == "cmdline_mismatch"
    evaluate.assert_called_once()


def test_adopt_backoff_is_invalidated_when_adopt_match_changes(tmp_path: Path) -> None:
    """exact→prefix 설정 변경은 동일 candidate라도 이전 mismatch cache를 재사용하지 않는다."""
    pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
    service = replace(_make_service(sid="mode", port=23456), adopt_command=("server", "start"))
    pid = os.getpid()
    candidate = _AdoptCandidate(pid=pid, pgid=os.getpgrp(), create_time=psutil.Process(pid).create_time())
    evaluation = AdoptEvaluation(
        diagnostics=AdoptDiagnostics(ok=False, reason="cmdline_mismatch", candidate_pid=pid),
        candidate=candidate,
        state_snapshot=RuntimeState(),
    )
    with patch.object(pm, "_evaluate_adopt", return_value=evaluation) as evaluate:
        pm.evaluate_adopt(service)
        pm.evaluate_adopt(replace(service, adopt_match="prefix"))
    assert evaluate.call_count == 2


def test_process_group_alive_skips_zombie_before_running_member(monkeypatch) -> None:
    """같은 PGID에 zombie와 running이 섞이면 running 멤버를 찾아 True여야 한다."""
    import process_manager as pm_module

    class FakeProc:
        def __init__(self, pid, status):
            self.pid = pid
            self._status = status

        def status(self):
            return self._status

    monkeypatch.setattr(
        pm_module.psutil,
        "process_iter",
        lambda _attrs: iter([FakeProc(1, psutil.STATUS_ZOMBIE), FakeProc(2, psutil.STATUS_RUNNING)]),
    )
    monkeypatch.setattr(pm_module.os, "getpgid", lambda _pid: 42)
    assert pm_module._process_group_is_alive(42) is True


def test_diagnose_adopt_reports_no_port_holder(tmp_path: Path) -> None:
    """포트가 비어 있으면 no_port_holder."""
    service = _make_service(sid="empty", port=_free_port())
    pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
    diag = pm.diagnose_adopt(service)
    assert diag.ok is False
    assert diag.reason == "no_port_holder"


def test_diagnose_adopt_reports_cmdline_mismatch(tmp_path: Path) -> None:
    """래퍼 명령과 실제 점유자 argv가 다르면 cmdline_mismatch.

    config는 `/bin/bash run.sh`인데 실제 포트 점유자는 다른 argv(여기선 listener)인
    상황을 재현한다. 진단에 expected_command와 actual_cmdline이 함께 담겨야 한다.
    """
    port = _free_port()
    cwd_for_child = str(tmp_path)
    listener = _spawn_listener(port, cwd=cwd_for_child)
    try:
        service = ServiceConfig(
            id="wrapper_mismatch",
            name="Wrapper Mismatch",
            description="",
            cwd=cwd_for_child,
            entry_file="run.sh",
            command=("/bin/bash", "run.sh"),  # 실제 점유자와 다른 명령
            env={},
            port=port,
            port_env_name=None,
            open_url=None,
            health=HealthConfig(
                enabled=True, type="http",
                url=f"http://127.0.0.1:{port}/health",
                timeout_seconds=2.0, verify_ssl=True,
            ),
            log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
            lifecycle=LifecycleConfig(
                mode="manual",
                autostart=False,
                stop_visibility="primary",
                restart_visibility="primary",
                unmanaged_policy="manage",
            ),
            actions=(),
        )
        pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
        diag = pm.diagnose_adopt(service)

        assert diag.ok is False
        assert diag.reason == "cmdline_mismatch"
        assert diag.candidate_pid == listener.pid
        assert diag.expected_command == ["/bin/bash", "run.sh"]
        # 실제 점유자의 argv가 그대로 노출돼야 "왜 안 됐나"를 바로 알 수 있다
        assert diag.actual_cmdline == psutil.Process(listener.pid).cmdline()
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def test_diagnose_adopt_resolves_with_adopt_command(tmp_path: Path) -> None:
    """adopt_command에 실제 점유자 argv를 넣으면 cmdline_mismatch가 해소된다."""
    port = _free_port()
    cwd_for_child = str(tmp_path)
    listener = _spawn_listener(port, cwd=cwd_for_child)
    try:
        actual_cmdline = tuple(psutil.Process(listener.pid).cmdline())
        service = ServiceConfig(
            id="adopt_command_match",
            name="Adopt Command Match",
            description="",
            cwd=cwd_for_child,
            entry_file="run.sh",
            command=("/bin/bash", "run.sh"),
            env={},
            port=port,
            port_env_name=None,
            open_url=None,
            health=HealthConfig(
                enabled=True, type="http",
                url=f"http://127.0.0.1:{port}/health",
                timeout_seconds=2.0, verify_ssl=True,
            ),
            log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
            lifecycle=LifecycleConfig(
                mode="manual",
                autostart=False,
                stop_visibility="primary",
                restart_visibility="primary",
                unmanaged_policy="manage",
            ),
            actions=(),
            adopt_command=actual_cmdline,  # 실제 점유자 argv 명시
        )
        pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
        diag = pm.diagnose_adopt(service)
        assert diag.ok is True
        assert diag.reason == "ok"
        assert diag.candidate_pid == listener.pid
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def test_diagnose_adopt_health_ok_override_skips_probe(tmp_path: Path) -> None:
    """health_ok를 명시하면 health URL을 다시 두드리지 않는다 (cmdline 통과 케이스)."""
    port = _free_port()
    cwd_for_child = str(tmp_path)
    listener = _spawn_listener(port, cwd=cwd_for_child)
    try:
        actual_cmdline = tuple(psutil.Process(listener.pid).cmdline())
        service = ServiceConfig(
            id="health_override",
            name="health_override",
            description="",
            cwd=cwd_for_child,
            entry_file="-",
            command=actual_cmdline,
            env={},
            port=port,
            port_env_name=None,
            open_url=None,
            # 일부러 닿지 않는 health URL. health_ok=True를 넘기면 무시돼야 함.
            health=HealthConfig(
                enabled=True, type="http",
                url="http://127.0.0.1:1/health",
                timeout_seconds=1.0, verify_ssl=True,
            ),
            log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
            lifecycle=LifecycleConfig(
                mode="manual",
                autostart=False,
                stop_visibility="primary",
                restart_visibility="primary",
                unmanaged_policy="manage",
            ),
            actions=(),
        )
        pm = ProcessManager(tmp_path / "runtime", LogManager(tmp_path / "logs"))
        diag = pm.diagnose_adopt(service, health_ok=True)
        assert diag.ok is True
        assert diag.health_ok is True
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


# ----------------------------------------------------------------------
# 9. v1.3.3: stop 블로킹 중 다른 서비스 상태 조회가 막히지 않음 (락 범위 축소)
# ----------------------------------------------------------------------


def _spawn_signal_ignoring_child() -> subprocess.Popen:
    """SIGTERM/SIGINT를 무시하는 자식. stop이 SIGKILL까지 가도록 강제한다."""
    code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "while True: time.sleep(0.1)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-u", "-c", code],
        start_new_session=True,
    )


def test_stop_does_not_block_other_service_status(tmp_path: Path) -> None:
    """한 서비스 stop이 대기하는 동안에도 다른 서비스 상태 조회가 즉시 반환된다.

    v1.3.3 이전엔 stop()이 self._lock을 시그널 시퀀스 전체 동안 잡아, 그동안 모든
    inspect_state/get_state가 막혔다. 이제 대기 구간은 락 밖에서 돈다.
    """
    import threading

    from config_loader import StopStrategy

    lm = LogManager(tmp_path / "logs")
    pm = ProcessManager(tmp_path / "runtime", lm)

    child = _spawn_signal_ignoring_child()
    try:
        # 자식이 SIG_IGN 핸들러를 설치할 시간을 준다 (그 전엔 SIGTERM에 즉사).
        time.sleep(0.6)
        pm._states["slow"] = RuntimeState(
            pid=child.pid,
            pgid=os.getpgid(child.pid),
            start_time=time.time(),
            create_time=psutil.Process(child.pid).create_time(),
            adopted=False,
            last_action="start",
        )
        service = ServiceConfig(
            id="slow",
            name="slow",
            description="",
            cwd=str(tmp_path),
            entry_file="-",
            command=("python3", "-u", "x.py"),
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
                unmanaged_policy="manage",
            ),
            actions=(),
        )
        # SIGTERM 무시 → 1.0초 대기 후 SIGKILL. stop은 최소 ~1초 걸린다.
        strategy = StopStrategy(
            signal="SIGTERM",
            timeout_seconds=1.0,
            confirm_required=False,
            fallback=("SIGKILL",),
        )

        stop_done = threading.Event()

        def run_stop():
            try:
                pm.stop(service, strategy)
            finally:
                stop_done.set()

        t = threading.Thread(target=run_stop)
        t.start()
        try:
            # stop이 대기 루프에 들어갈 시간을 준다.
            time.sleep(0.3)
            assert not stop_done.is_set(), "stop이 너무 빨리 끝나 테스트 의미 없음"

            # 다른 서비스(여기선 같은 PM의 별도 id) 상태 조회가 즉시 반환돼야 한다.
            started = time.perf_counter()
            pm.get_state("other")
            pm.inspect_state("other")  # pid 없음 → 즉시 반환
            elapsed = time.perf_counter() - started
            assert elapsed < 0.3, f"상태 조회가 stop 락에 막힘: {elapsed:.3f}s"
        finally:
            t.join(timeout=10)
        assert stop_done.is_set()
        assert pm.get_state("slow").pid is None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
