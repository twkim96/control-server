"""서비스 프로세스 시작/중지/재시작 관리.

핵심 정책:

* `subprocess.Popen(..., shell=False, start_new_session=True)` 사용.
  자식은 새 process group으로 띄워진다.
* stdout/stderr는 LogManager가 제공하는 파일 핸들로 redirect.
* 시작 시 PID와 start_time, psutil.create_time을 runtime/<id>.json에 저장.
  PID 재사용 오판을 막기 위해 상태 조회 때 create_time을 함께 비교한다.
* 중지는 설정된 signal(기본 SIGINT) → fallback (SIGTERM, SIGKILL) 순서.
  process group(pgid)에 시그널을 보내 부모와 자식이 같은 그룹에 있으면 함께 정리된다.
* 자식을 별도 session으로 띄우는 서비스는 부모만 정리하면
  부모가 자체 핸들러로 자식을 정리한다.

동시성 가정: 컨트롤 서버는 single worker로 실행된다.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from threading import RLock
from typing import Any, Callable

import psutil

from config_loader import ServiceConfig, StopStrategy
from log_manager import LogManager
from net_utils import (
    find_port_holder as _find_port_holder,
    holder_via_lsof as _holder_via_lsof,
    holder_via_psutil as _holder_via_psutil,
    parse_tcp_target as _parse_tcp_target,
    pid_alive as _pid_alive,
    port_accepts_connections as _port_accepts_connections,
    tcp_probe as _tcp_probe,
)

_log = logging.getLogger("server_control.process")


# ----------------------------------------------------------------------
# 예외 / 데이터 모델
# ----------------------------------------------------------------------


class ProcessError(RuntimeError):
    """프로세스 관리 단계의 기대 가능한 오류."""


@dataclass
class RuntimeState:
    """디스크에 저장되는 프로세스 런타임 상태.

    `pid`가 None이면 컨트롤 서버가 추적 중인 프로세스가 없다는 뜻이다.
    `adopted`가 True면 외부에서 떠있던 자식을 입양한 상태이며, stop 시 process group
    단위 시그널 대신 PID 단위 시그널을 사용한다 (group 안에 다른 프로세스가 있을 위험).
    """

    pid: int | None = None
    pgid: int | None = None
    start_time: float | None = None  # epoch seconds, 컨트롤 서버 관점
    create_time: float | None = None  # psutil의 process create_time, stale PID 검증용
    last_exit_code: int | None = None
    last_exit_time: float | None = None
    last_action: str | None = None
    last_action_time: float | None = None
    adopted: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "RuntimeState":
        data = json.loads(text)
        # 누락된 필드는 default 사용
        kwargs = {k: data.get(k, getattr(cls(), k)) for k in cls().__dataclass_fields__}
        # v1.2.5 → v1.2.6 마이그레이션:
        # 정식 `adopted` 필드 도입 전엔 입양 표식을 `extra={"adopted": True}`로 저장했다.
        # JSON에 정식 adopted 키가 없는데 extra.adopted가 True면 정식 필드로 승격하고
        # extra에서는 제거해 두 자리에 같은 정보가 사는 혼란을 막는다.
        if "adopted" not in data:
            extra = kwargs.get("extra") or {}
            if isinstance(extra, dict) and extra.get("adopted") is True:
                kwargs["adopted"] = True
                # 원본 extra를 건드리지 않고 사본에서 제거
                cleaned = {k: v for k, v in extra.items() if k != "adopted"}
                kwargs["extra"] = cleaned
        return cls(**kwargs)


@dataclass(frozen=True)
class AdoptDiagnostics:
    """`try_adopt` 한 번의 평가 결과와 실패 사유.

    `ok`가 False면 `reason`이 어느 단계에서 막혔는지 알려준다. UI/외부 클라이언트가
    "왜 입양이 안 되고 running_external로만 보이나"에 바로 답할 수 있도록 runtime
    payload에 그대로 직렬화된다.

    reason 값:
    * ok                  : 모든 조건 통과 (입양 가능/성공)
    * policy_not_manage   : lifecycle.unmanaged_policy != "manage"
    * no_port             : service.port 미설정 → 점유자 식별 불가
    * no_port_holder      : 포트를 LISTEN하는 프로세스를 못 찾음
    * already_tracked     : 이미 컨트롤 서버가 추적 중 (입양 불필요)
    * pid_is_self         : 점유자가 컨트롤 서버 자신
    * pid_tracked_by_other: 점유자가 다른 서비스로 추적 중인 PID
    * proc_inspect_failed : 점유 PID의 cmdline/cwd 조회 실패 (권한/소멸 등)
    * cmdline_mismatch    : 실제 argv가 command/adopt_command와 불일치
    * cwd_resolve_failed  : cwd realpath 변환 실패
    * cwd_mismatch        : 실제 cwd가 service.cwd와 불일치
    * health_failed       : health.enabled인데 health URL이 2xx를 안 줌
    * pm2_exclusive       : PM2가 유일한 소유자라 외부 PID 자동 입양을 하지 않음
    """

    ok: bool
    reason: str
    candidate_pid: int | None = None
    expected_command: list[str] | None = None
    adopt_command: list[str] | None = None
    actual_cmdline: list[str] | None = None
    cwd_expected: str | None = None
    cwd_actual: str | None = None
    health_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _AdoptCandidate:
    """입양 평가가 성공했을 때 RuntimeState 구성에 필요한 실측값."""

    pid: int
    pgid: int
    create_time: float | None


@dataclass(frozen=True)
class AdoptEvaluation:
    """락 밖에서 수행한 입양 관찰과 그 시점의 service state snapshot."""

    diagnostics: AdoptDiagnostics
    candidate: _AdoptCandidate | None
    state_snapshot: RuntimeState


# ----------------------------------------------------------------------
# 시그널 이름 → 번호 매핑
# ----------------------------------------------------------------------


def _signal_for(name: str) -> int:
    name = name.upper()
    sig = getattr(signal, name, None)
    if not isinstance(sig, signal.Signals):
        raise ProcessError(f"알 수 없는 시그널 이름: {name!r}")
    return int(sig)


# ----------------------------------------------------------------------
# ProcessManager
# ----------------------------------------------------------------------


class ProcessManager:
    def __init__(
        self,
        runtime_root: str | os.PathLike[str],
        log_manager: LogManager,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(runtime_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._log = log_manager
        self._lock = RLock()
        # 서비스별 작업(start/stop/restart) 직렬화용 락.
        # self._lock(상태 dict 보호)과 분리해, 긴 블로킹 구간(시그널 대기, lsof 등)에서는
        # self._lock을 놓고 op-lock만 잡는다. 그래야 한 서비스 stop 중에도 다른 서비스의
        # 상태 폴링이 self._lock에서 막히지 않는다.
        self._op_locks: dict[str, RLock] = {}
        self._op_locks_guard = RLock()
        self._retired_services: set[str] = set()
        # config API가 활성화한 현재 정의. 직접 ProcessManager를 쓰는 기존 단위 테스트는
        # 항목이 없으므로 그대로 동작하고, 서버 경로에서는 stale ServiceConfig를 막는다.
        self._active_services: dict[str, ServiceConfig] = {}
        self._adopt_observation_cache: dict[tuple[Any, ...], tuple[float, AdoptEvaluation]] = {}
        self._states: dict[str, RuntimeState] = {}
        self._popens: dict[str, subprocess.Popen] = {}
        self._clock = clock
        # 시작 직후 디스크에서 기존 상태 복구 (컨트롤 서버 재시작 시나리오 대비)
        self._load_all()

    def _op_lock(self, service_id: str) -> RLock:
        """서비스별 작업 직렬화 락을 가져온다 (없으면 생성).

        RLock이라 restart처럼 같은 스레드가 stop→start를 연쇄 호출해도 재진입 가능하다.
        """
        with self._op_locks_guard:
            lock = self._op_locks.get(service_id)
            if lock is None:
                lock = RLock()
                self._op_locks[service_id] = lock
            return lock

    @contextmanager
    def service_operation(self, service_id: str):
        """config 삭제 등 service lifecycle 전체를 start/stop/adopt와 직렬화한다."""
        with self._op_lock(service_id):
            yield

    def activate_service(self, service: ServiceConfig) -> None:
        """새 config 정의를 활성화해 같은 ID의 stale 작업을 차단한다."""
        with self._op_lock(service.id):
            self._retired_services.discard(service.id)
            self._active_services[service.id] = service

    def reconcile_service_definitions(self, services: tuple[ServiceConfig, ...]) -> None:
        """수동 config reload의 정의를 ProcessManager와 함께 갱신한다."""
        incoming = {service.id: service for service in services}
        with self._lock:
            known_ids = set(self._active_services) | set(self._states)
            removed = known_ids - set(incoming)
        # 제거 판단과 retire를 같은 service op-lock 안에서 수행한다. start/adopt가 그
        # 사이 state를 저장해도 live service를 orphan으로 만들지 않는다.
        for sid in sorted(removed):
            with self._op_lock(sid):
                with self._lock:
                    state = self._states.get(sid, RuntimeState())
                if self._is_alive(state):
                    raise ProcessError(f"{sid}가 실행 중이라 config reload에서 제거할 수 없습니다.")
                with self._lock:
                    self._active_services.pop(sid, None)
                    self._retired_services.add(sid)
        for service in services:
            self.activate_service(service)

    def forget_service(self, service_id: str) -> None:
        """stopped service의 runtime/Popen/runtime JSON을 제거하고 ID를 retire한다."""
        with self._op_lock(service_id):
            with self._lock:
                state = self._states.get(service_id, RuntimeState())
            if self._is_alive(state):
                raise ProcessError(f"{service_id}는 실행 중이라 삭제할 수 없습니다. 먼저 중지하세요.")
            with self._lock:
                current = self._states.get(service_id, RuntimeState())
                if current.pid != state.pid or current.create_time != state.create_time:
                    raise ProcessError(f"{service_id} 상태가 삭제 중 변경되었습니다. 다시 시도하세요.")
                self._states.pop(service_id, None)
                self._popens.pop(service_id, None)
                self._retired_services.add(service_id)
                self._active_services.pop(service_id, None)
                self._state_path(service_id).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # 디스크 영속
    # ------------------------------------------------------------------

    def _state_path(self, service_id: str) -> Path:
        return self._root / f"{service_id}.json"

    def _load_all(self) -> None:
        for path in self._root.glob("*.json"):
            try:
                state = RuntimeState.from_json(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            self._states[path.stem] = state

    def _save(self, service_id: str, state: RuntimeState) -> None:
        path = self._state_path(service_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(state.to_json(), encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------------
    # 상태 조회
    # ------------------------------------------------------------------

    def get_state(self, service_id: str) -> RuntimeState:
        with self._lock:
            return self._states.setdefault(service_id, RuntimeState())

    def is_alive(self, service_id: str) -> bool:
        """추적 중인 PID가 진짜로 살아있는지 확인.

        PID가 같더라도 create_time이 다르면 OS가 PID를 재사용한 것이므로
        죽은 것으로 간주한다.
        """
        state = self.get_state(service_id)
        return self._is_alive(state)

    def _is_alive(self, state: RuntimeState) -> bool:
        """추적 PID가 살아있는지 보수적으로 판정.

        v1.2.5 원칙: **확실히 죽었을 때만 dead로 단정**. 일과성 OS 오류(macOS 절전/wake
        시 psutil이 일시적으로 정보를 못 가져오는 경우 등)에 mark_stopped로 잘못 정리하면
        멀쩡한 자식의 추적이 끊긴다. 그래서 의심스러운 신호는 모두 alive로 본다.

        - `NoSuchProcess`만 진짜 dead.
        - `AccessDenied`나 다른 OSError는 alive로 간주 (정보 부족 = 살아있다고 본다).
        - create_time mismatch는 즉시 dead로 단정하지 않고, 짧게 한 번 더 조회한 뒤 두
          번 다 mismatch일 때만 dead.
        """
        if state.pid is None:
            return False
        try:
            proc = psutil.Process(state.pid)
        except psutil.NoSuchProcess:
            return False
        except (psutil.AccessDenied, OSError):
            # 정보 못 가져옴. 죽었다고 단정하지 않는다.
            return True

        if state.create_time is None:
            try:
                return proc.is_running()
            except (psutil.NoSuchProcess,):
                return False
            except (psutil.AccessDenied, OSError):
                return True

        if not _create_time_matches(proc, state.create_time):
            # 한 번 mismatch면 짧게 기다렸다가 한 번 더. 일과성 오류 방지.
            time.sleep(0.2)
            try:
                proc = psutil.Process(state.pid)
            except psutil.NoSuchProcess:
                return False
            except (psutil.AccessDenied, OSError):
                return True
            if not _create_time_matches(proc, state.create_time):
                return False

        try:
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except (psutil.AccessDenied, OSError):
            return True

    # ------------------------------------------------------------------
    # 시작
    # ------------------------------------------------------------------

    def start(self, service: ServiceConfig) -> RuntimeState:
        # op-lock으로 같은 서비스의 start/stop을 직렬화한다. 블로킹 preflight
        # (alive 확인, 포트 점유 lsof, Popen)는 self._lock 밖에서 수행하고,
        # 상태 커밋만 self._lock 짧게 잡는다.
        with self._op_lock(service.id):
            if service.id in self._retired_services:
                raise ProcessError(f"{service.id}는 삭제된 서비스입니다. 새 설정을 다시 불러오세요.")
            active = self._active_services.get(service.id)
            if active is not None and active != service:
                raise ProcessError(f"{service.id}의 설정이 변경되었습니다. 최신 설정으로 다시 시도하세요.")
            with self._lock:
                state = self.get_state(service.id)
            if self._is_alive(state):
                raise ProcessError(f"{service.id}는 이미 실행 중입니다 (pid={state.pid}).")

            cwd = Path(service.cwd)
            if not cwd.is_dir():
                raise ProcessError(f"working directory가 존재하지 않습니다: {cwd}")

            # 시작 직전 포트 점유 확인.
            # 누가 설정된 서비스 포트를 이미 잡고 있으면 자식이 즉시 죽는다.
            # 사용자가 원인을 모를 수 있으므로 미리 거부한다.
            # (lsof 폴백은 최대 2초까지 걸릴 수 있어 self._lock 밖에서 수행한다.)
            if service.port is not None:
                holder = _find_port_holder(service.port)
                if holder is not None:
                    raise ProcessError(
                        f"{service.id}: 포트 {service.port}이(가) 이미 사용 중입니다 "
                        f"(pid={holder['pid']}, name={holder['name']}). "
                        "기존 프로세스를 먼저 종료하세요."
                    )

            env = os.environ.copy()
            env.update(service.env)
            # port_env_name이 있으면 service.port를 자동으로 자식 환경변수로 주입.
            # 사용자가 env에 같은 키를 명시했다면 그 값이 우선한다.
            if service.port_env_name and service.port is not None:
                if service.port_env_name not in service.env:
                    env[service.port_env_name] = str(service.port)
            if service.https.enabled:
                if service.https.enabled_env_name not in service.env:
                    env[service.https.enabled_env_name] = "1"
                if service.https.cert_file and service.https.cert_file_env_name not in service.env:
                    env[service.https.cert_file_env_name] = _resolve_child_path(
                        cwd, service.https.cert_file
                    )
                if service.https.key_file and service.https.key_file_env_name not in service.env:
                    env[service.https.key_file_env_name] = _resolve_child_path(
                        cwd, service.https.key_file
                    )
            # 자식이 Python 서버라도 실시간 로그 보장
            env.setdefault("PYTHONUNBUFFERED", "1")

            try:
                # 자식 stdout/stderr를 LogManager가 만들어준 fd로 직접 redirect.
                # 이렇게 하면 컨트롤 서버 프로세스를 거치지 않고 OS가 fd를 잡고 있어,
                # 컨트롤 서버가 죽어도 자식이 SIGPIPE로 같이 죽는 일이 없다.
                child_fd = self._log.open_for_child(
                    service.id,
                    max_bytes=service.log.max_bytes,
                    keep=service.log.keep,
                )
                try:
                    popen = subprocess.Popen(
                        list(service.command),
                        cwd=str(cwd),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=child_fd,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                        shell=False,
                    )
                finally:
                    # Popen이 fd를 dup해서 자식 측 fd로 사용하므로 부모 측 fd는 즉시 닫는다.
                    # 이렇게 안 두면 부모 입장에서도 fd가 열려있어 자식이 죽어도 reader가
                    # EOF를 못 받지만, 어차피 reader thread가 없으므로 문제는 없고
                    # 단지 fd 누수만 막는 의도.
                    try:
                        os.close(child_fd)
                    except OSError:
                        pass
            except (OSError, FileNotFoundError) as exc:
                raise ProcessError(f"{service.id} 시작 실패: {exc}") from exc

            try:
                proc = psutil.Process(popen.pid)
                create_time = proc.create_time()
            except psutil.NoSuchProcess:
                # 매우 짧은 시간 안에 죽은 경우
                create_time = None

            now = self._clock()
            new_state = RuntimeState(
                pid=popen.pid,
                pgid=os.getpgid(popen.pid),
                start_time=now,
                create_time=create_time,
                last_exit_code=None,
                last_exit_time=None,
                last_action="start",
                last_action_time=now,
                adopted=False,
                extra={},
            )
            with self._lock:
                self._states[service.id] = new_state
                self._popens[service.id] = popen
                self._save(service.id, new_state)
            return new_state

    # ------------------------------------------------------------------
    # 중지
    # ------------------------------------------------------------------

    def stop(
        self,
        service: ServiceConfig,
        strategy: StopStrategy,
        *,
        poll_interval: float = 0.2,
    ) -> RuntimeState:
        # op-lock으로 같은 서비스의 start/stop을 직렬화. 시그널 시퀀스 대기는
        # self._lock 밖에서 수행해, stop이 길어도(SIGKILL까지 최대 timeout+2초) 다른
        # 서비스의 상태 폴링이 self._lock에서 막히지 않게 한다.
        with self._op_lock(service.id):
            with self._lock:
                state = self.get_state(service.id)
            use_group_kill = _can_use_group_kill(state)
            if not self._stop_target_is_alive(state, use_group_kill):
                # 이미 죽었으면 상태만 정리하고 반환
                if state.pid is not None:
                    self._mark_stopped_if_current(
                        service.id,
                        expected_pid=state.pid,
                        expected_create_time=state.create_time,
                        exit_code=None,
                    )
                return self.get_state(service.id)

            assert state.pid is not None  # is_alive가 True이므로
            assert state.pgid is not None

            primary = _signal_for(strategy.signal)
            fallbacks = [_signal_for(s) for s in strategy.fallback]
            timeout = strategy.timeout_seconds

            sequence: list[tuple[int, float]] = [(primary, timeout)]
            for sig in fallbacks:
                # SIGKILL은 마지막에 짧은 대기로 충분
                wait = timeout if sig != signal.SIGKILL else 2.0
                sequence.append((sig, wait))

            exit_code: int | None = None
            # 시그널 전송 정책:
            # - 우리가 직접 spawn한 자식: process group(pgid)에 시그널을 보내 부모와
            #   같은 group에 있는 자식들까지 한 번에 정리한다.
            # - 입양한 외부 자식: 그 자식의 pgid 안에 다른 프로세스(최악의 경우 컨트롤
            #   서버 자신이나 사용자 터미널)가 함께 있을 수 있다. group kill은 위험하므로
            #   PID 단위 시그널만 보낸다. 자식이 자기 자식을 정리하는 책임은 자식의
            #   SIGINT/SIGTERM 핸들러에 맡긴다.
            for index, (sig, wait) in enumerate(sequence):
                if index == 0 and use_group_kill and not _group_leader_identity_is_current(state):
                    raise ProcessError(
                        f"{service.id} process group leader 신원을 확인할 수 없어 중지하지 않았습니다."
                    )
                if not use_group_kill and not _pid_identity_is_current(state):
                    # PID가 사라졌거나 재사용됐다. 새 PID에는 signal을 보내지 않고 원래
                    # 대상이 종료된 것으로 처리한다.
                    break
                try:
                    if use_group_kill:
                        os.killpg(state.pgid, sig)
                    else:
                        os.kill(state.pid, sig)
                except ProcessLookupError:
                    # 이미 죽었음
                    exit_code = self._poll_popen(service.id)
                    break

                exit_code = self._wait_for_exit(
                    service.id,
                    state,
                    wait,
                    poll_interval,
                    use_group_kill=use_group_kill,
                )
                if not self._stop_target_is_alive(state, use_group_kill):
                    break

            # 실패 (죽지 않음) -> 호출자에게 알림. 상태는 그대로 둔다.
            if self._stop_target_is_alive(state, use_group_kill):
                raise ProcessError(
                    f"{service.id} 중지 실패: SIGKILL 후에도 살아있습니다 (pid={state.pid})."
                )

            self._mark_stopped_if_current(
                service.id,
                expected_pid=state.pid,
                expected_create_time=state.create_time,
                exit_code=exit_code,
            )
            return self.get_state(service.id)

    def _wait_for_exit(
        self,
        service_id: str,
        state: RuntimeState,
        timeout: float,
        poll_interval: float,
        *,
        use_group_kill: bool,
    ) -> int | None:
        """timeout 안에 자식이 종료되는지 폴링.

        직접 spawn한 service는 leader Popen이 끝났더라도 같은 process group의 자식이
        남아 있으면 아직 종료로 보지 않는다. 입양 프로세스는 안전상 PID 단위로만 검사한다.
        """
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            code = self._poll_popen(service_id)
            if not self._stop_target_is_alive(state, use_group_kill):
                return code
            time.sleep(poll_interval)
        return self._poll_popen(service_id)

    def _stop_target_is_alive(self, state: RuntimeState, use_group_kill: bool) -> bool:
        """stop 성공 조건에 맞는 대상 생존 여부를 반환한다."""
        if use_group_kill:
            assert state.pgid is not None
            return _process_group_is_alive(state.pgid)
        return self._is_alive(state)

    def _poll_popen(self, service_id: str) -> int | None:
        with self._lock:
            popen = self._popens.get(service_id)
        if popen is None:
            return None
        return popen.poll()

    def _mark_stopped_if_current(
        self,
        service_id: str,
        *,
        expected_pid: int,
        expected_create_time: float | None,
        exit_code: int | None,
    ) -> bool:
        """현재 runtime 신원이 관찰한 대상과 같을 때만 stopped를 커밋한다.

        상태 검사처럼 락 밖에서 오래 걸린 관찰을 한 뒤에는, 그 사이 start/adopt가 새
        상태를 저장했을 수 있다. PID와 create_time을 같은 락 임계구역에서 비교하고
        stopped state, Popen 제거, runtime JSON 저장을 함께 수행해 오래된 관찰이 새
        상태를 지우지 못하게 한다. 로그 회전은 성공한 커밋 뒤에만 락 밖에서 실행한다.
        """
        committed = False
        with self._lock:
            current = self._states.get(service_id)
            if (
                current is None
                or current.pid != expected_pid
                or current.create_time != expected_create_time
            ):
                return False

            now = self._clock()
            stopped = RuntimeState(
                pid=None,
                pgid=None,
                start_time=None,
                create_time=None,
                last_exit_code=exit_code,
                last_exit_time=now,
                last_action=current.last_action,
                last_action_time=now,
                adopted=False,
                extra=current.extra,
            )
            self._states[service_id] = stopped
            self._popens.pop(service_id, None)
            self._save(service_id, stopped)
            committed = True
        # LogManager는 자체 락을 가지므로 self._lock 밖에서 호출.
        if committed:
            self._log.stop_capture(service_id)
        return committed

    # ------------------------------------------------------------------
    # 재시작
    # ------------------------------------------------------------------

    def restart(self, service: ServiceConfig, strategy: StopStrategy) -> RuntimeState:
        """stop이 정상 완료된 경우에만 start를 시도한다."""
        # op-lock(RLock)을 잡아 stop→start 사이에 다른 작업이 끼어들지 못하게 한다.
        # RLock이라 내부 stop/start의 재진입은 안전하다.
        with self._op_lock(service.id):
            try:
                self.stop(service, strategy)
            except ProcessError:
                # stop 실패 시 start를 시도하지 않는다.
                raise
            return self.start(service)

    # ------------------------------------------------------------------
    # 죽은 자식 자동 정리
    # ------------------------------------------------------------------

    def inspect_state(self, service_id: str) -> tuple[RuntimeState, bool]:
        """현재 상태를 한 번 검사하고 필요하면 stopped로 정리한다.

        반환값의 bool은 같은 검사에서 확인한 alive 여부다. 호출자가 get_state()와
        is_alive()를 연달아 호출해 같은 PID를 중복 조회하지 않도록 묶어서 제공한다.

        v1.3.3: alive 판정의 블로킹 구간(time.sleep + _is_alive 내부 재시도)은 self._lock
        밖에서 수행한다. 이 메서드는 매 상태 폴링마다 호출되므로, 락을 쥔 채 sleep하면
        그 사이 모든 서비스의 상태 조회가 막힌다.
        """
        with self._lock:
            state = self._states.get(service_id)
            if state is None or state.pid is None:
                return state or RuntimeState(), False

        # 블로킹 검사는 락 밖에서. state는 스냅샷이라 안전하다.
        if self._is_alive(state):
            return state, True

        # 한 번 더 확인. _is_alive 내부에도 short retry가 있지만, 이 단계에서
        # 추가로 한 번 더 보는 게 정리 사고 방지에 안전망 역할.
        time.sleep(0.3)
        if self._is_alive(state):
            return state, True

        exit_code = self._poll_popen(service_id)
        if not self._mark_stopped_if_current(
            service_id,
            expected_pid=state.pid,
            expected_create_time=state.create_time,
            exit_code=exit_code,
        ):
            # CAS 실패는 관찰 중 새 state가 저장된 경우다. 최신 state는 그대로 보존하고
            # 다음 poll이 이를 다시 평가하게 한다.
            with self._lock:
                current = self._states.get(service_id)
                if current is None:
                    return RuntimeState(), False
                return current, current.pid is not None
        with self._lock:
            return self._states[service_id], False

    def reap_if_dead(self, service_id: str) -> bool:
        """추적 중인 PID가 죽었으면 상태를 stopped로 정리하고 True 반환."""
        with self._lock:
            state = self._states.get(service_id)
            had_pid = state is not None and state.pid is not None
        # inspect_state는 내부에서 블로킹 검사를 self._lock 밖으로 빼므로, 여기서도
        # 락을 쥔 채 호출하지 않는다 (쥐고 호출하면 sleep 동안 락 점유).
        _state, alive = self.inspect_state(service_id)
        return had_pid and not alive

    # ------------------------------------------------------------------
    # 자동 입양 (외부 인스턴스를 추적 대상으로 받아들이기)
    # ------------------------------------------------------------------

    def try_adopt(
        self,
        service: ServiceConfig,
        *,
        evaluation: AdoptEvaluation | None = None,
        health_ok: bool | None = None,
    ) -> RuntimeState | None:
        """외부에서 떠있는 자식을 추적 대상으로 받아들인다.

        실제 신뢰 조건 평가는 `_evaluate_adopt`에 위임한다 (startup이든 health 경로든
        같은 기준이 적용되도록). 평가가 통과하면 runtime json에
        PID/create_time/start_time/adopted=True를 저장하고 RuntimeState를 반환한다.
        실패하면 None을 반환하며, 사유는 `diagnose_adopt`로 따로 조회할 수 있다.
        """
        evaluation = evaluation or self.evaluate_adopt(service, health_ok=health_ok)
        if not evaluation.diagnostics.ok or evaluation.candidate is None:
            # 이 함수는 health 폴링마다 호출될 수 있어 INFO 로깅은 시끄럽다.
            # 사유는 payload(diagnose_adopt)로 노출하고 여기서는 debug만.
            _log.debug("adopt skip: %s reason=%s", service.id, evaluation.diagnostics.reason)
            return None
        return self._commit_adopt_evaluation(service, evaluation)

    def _commit_adopt_evaluation(
        self, service: ServiceConfig, evaluation: AdoptEvaluation
    ) -> RuntimeState | None:
        """평가 snapshot이 여전히 유효한 경우에만 fresh identity로 입양을 커밋한다."""
        candidate = evaluation.candidate
        if candidate is None or service.port is None:
            return None
        with self._op_lock(service.id):
            if service.id in self._retired_services:
                return None
            active = self._active_services.get(service.id)
            if active is not None and active != service:
                return None
            # 평가와 commit 사이 port holder/PID가 바뀌었는지 fresh 확인한다.
            holder = _find_port_holder(service.port)
            if holder is None or holder.get("pid") != candidate.pid:
                return None
            try:
                fresh_create_time = psutil.Process(candidate.pid).create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                return None
            if candidate.create_time is None or abs(fresh_create_time - candidate.create_time) > 1.0:
                return None

            now = self._clock()
            with self._lock:
                current = self._states.get(service.id, RuntimeState())
                if current != evaluation.state_snapshot:
                    return None
                for sid, other in self._states.items():
                    if sid == service.id or other.pid != candidate.pid:
                        continue
                    if other.create_time is None or abs(other.create_time - fresh_create_time) <= 1.0:
                        return None

                adopted = RuntimeState(
                    pid=candidate.pid,
                    pgid=candidate.pgid,
                    start_time=candidate.create_time,
                    create_time=candidate.create_time,
                    last_exit_code=current.last_exit_code,
                    last_exit_time=current.last_exit_time,
                    last_action="adopt",
                    last_action_time=now,
                    adopted=True,
                    extra=dict(current.extra),
                )
                self._states[service.id] = adopted
                self._popens.pop(service.id, None)
                self._save(service.id, adopted)
                return adopted

    # ------------------------------------------------------------------
    # 입양 관찰
    # ------------------------------------------------------------------

    def diagnose_adopt(
        self,
        service: ServiceConfig,
        *,
        health_ok: bool | None = None,
    ) -> AdoptDiagnostics:
        """입양 가능 여부를 부작용 없이 평가해 사유를 반환한다.

        running_external 상태에서 "왜 자동 입양이 안 됐나"를 UI/외부 클라이언트가
        바로 알 수 있도록 runtime payload에 실어 보내기 위한 진입점.

        `health_ok`를 넘기면 health URL을 다시 두드리지 않고 그 값을 사용한다
        (호출자가 직전에 이미 health를 확인한 경우 중복 요청 방지).
        """
        return self.evaluate_adopt(service, health_ok=health_ok).diagnostics

    def evaluate_adopt(
        self, service: ServiceConfig, *, health_ok: bool | None = None
    ) -> AdoptEvaluation:
        """한 요청 안에서 health/route가 재사용할 부작용 없는 입양 관찰 결과."""
        signature = _adopt_service_signature(service)
        now = time.monotonic()
        with self._lock:
            cached = self._adopt_observation_cache.get(signature)
        if cached is not None and now < cached[0]:
            evaluation = cached[1]
            candidate = evaluation.candidate
            if candidate is None or _candidate_identity_is_alive(candidate):
                return evaluation
        evaluation = self._evaluate_adopt(service, health_ok=health_ok)
        delay = _adopt_backoff_seconds(evaluation.diagnostics.reason)
        if delay > 0:
            with self._lock:
                self._adopt_observation_cache[signature] = (now + delay, evaluation)
        return evaluation

    def _evaluate_adopt(
        self,
        service: ServiceConfig,
        *,
        health_ok: bool | None = None,
    ) -> AdoptEvaluation:
        """입양 신뢰 조건을 순서대로 검증한다 (v1.2.6 기준).

        1. service.lifecycle.unmanaged_policy == "manage"
        2. service.port가 정의되어 있고 그 포트를 점유하는 PID를 찾을 수 있음
        3. 그 PID가 자기 자신이나 다른 추적 중 PID가 아님
        4. cmdline이 service.command 또는 명시된 service.adopt_command와 정확히 매치
        5. proc.cwd()의 realpath가 service.cwd의 realpath와 일치
        6. service.health.enabled=True 면 health URL이 2xx 응답

        반환: (AdoptDiagnostics, 성공 시 _AdoptCandidate / 실패 시 None).
        """
        expected_command = list(service.command)
        adopt_command = (
            list(service.adopt_command) if service.adopt_command is not None else None
        )
        try:
            cwd_expected = os.path.realpath(service.cwd)
        except OSError:
            cwd_expected = service.cwd

        with self._lock:
            current = self._states.get(service.id, RuntimeState())
            state_snapshot = replace(current, extra=dict(current.extra))

        def fail(
            reason: str,
            *,
            candidate: _AdoptCandidate | None = None,
            candidate_pid: int | None = None,
            actual_cmdline: list[str] | None = None,
            cwd_actual: str | None = None,
            resolved_health: bool | None = None,
        ) -> AdoptEvaluation:
            return AdoptEvaluation(
                diagnostics=AdoptDiagnostics(
                    ok=False,
                    reason=reason,
                    candidate_pid=candidate_pid,
                    expected_command=expected_command,
                    adopt_command=adopt_command,
                    actual_cmdline=actual_cmdline,
                    cwd_expected=cwd_expected,
                    cwd_actual=cwd_actual,
                    health_ok=resolved_health,
                ),
                candidate=candidate,
                state_snapshot=state_snapshot,
            )

        policy_not_manage = service.lifecycle.unmanaged_policy != "manage"
        if service.port is None:
            return fail("policy_not_manage" if policy_not_manage else "no_port")

        if self._is_alive(state_snapshot):
            return fail("already_tracked")

        holder = _find_port_holder(service.port)
        if holder is None or holder.get("pid") is None:
            return fail("policy_not_manage" if policy_not_manage else "no_port_holder")
        candidate_pid = int(holder["pid"])

        # status_only도 holder PID는 관찰 결과로 제공하되, 입양 신뢰 검증/commit은
        # 수행하지 않는다. 이렇게 해야 목록의 unmanaged_pid와 상세 diagnostics가 같은
        # 단 한 번의 port scan을 공유한다.
        if policy_not_manage:
            return fail("policy_not_manage", candidate_pid=candidate_pid)

        # 안전 가드: 자기 자신이나 다른 추적 중 PID는 거부.
        if candidate_pid == os.getpid():
            return fail("pid_is_self", candidate_pid=candidate_pid)
        # cmdline + cwd + create_time + pgid 한 번에 안전하게 가져오기.
        try:
            proc = psutil.Process(candidate_pid)
            actual_cmdline = list(proc.cmdline())
            actual_cwd = proc.cwd()
            create_time = proc.create_time()
            pgid = os.getpgid(candidate_pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ProcessLookupError):
            return fail("proc_inspect_failed", candidate_pid=candidate_pid)
        observed_candidate = _AdoptCandidate(
            pid=candidate_pid, pgid=pgid, create_time=create_time
        )

        with self._lock:
            for sid, other in self._states.items():
                if sid == service.id or other.pid != candidate_pid:
                    continue
                if other.create_time is None or abs(other.create_time - create_time) <= 1.0:
                    return fail("pid_tracked_by_other", candidate_pid=candidate_pid)

        if not _service_cmdline_match(service, actual_cmdline):
            return fail(
                "cmdline_mismatch",
                candidate=observed_candidate,
                candidate_pid=candidate_pid,
                actual_cmdline=actual_cmdline,
            )

        # cwd 일치 검증. realpath 기반으로 symlink 차이를 흡수하고,
        # PATH상 바이너리(예: brew /opt/homebrew/bin/sunshine → Cellar/.../bin/sunshine)는
        # command[0]의 실제 dirname도 후보로 인정한다.
        try:
            actual_cwd_real = os.path.realpath(actual_cwd)
        except OSError:
            return fail(
                "cwd_resolve_failed",
                candidate=observed_candidate,
                candidate_pid=candidate_pid,
                actual_cmdline=actual_cmdline,
            )
        if cwd_expected != actual_cwd_real and not _command_dir_matches_actual_cwd(
            service.command, actual_cwd_real
        ):
            return fail(
                "cwd_mismatch",
                candidate=observed_candidate,
                candidate_pid=candidate_pid,
                actual_cmdline=actual_cmdline,
                cwd_actual=actual_cwd_real,
            )

        # health URL이 활성화되어 있으면 2xx 응답을 한 번 더 확인. health_checker와
        # 무관하게 _evaluate_adopt 단독 호출자(startup pass 등)도 같은 기준을 통과시키기 위함.
        resolved_health: bool | None = None
        if service.health.enabled:
            resolved_health = (
                health_ok if health_ok is not None else _adopt_health_ok(service.health)
            )
            if not resolved_health:
                return fail(
                    "health_failed",
                    candidate=observed_candidate,
                    candidate_pid=candidate_pid,
                    actual_cmdline=actual_cmdline,
                    cwd_actual=actual_cwd_real,
                    resolved_health=False,
                )

        diag = AdoptDiagnostics(
            ok=True,
            reason="ok",
            candidate_pid=candidate_pid,
            expected_command=expected_command,
            adopt_command=adopt_command,
            actual_cmdline=actual_cmdline,
            cwd_expected=cwd_expected,
            cwd_actual=actual_cwd_real,
            health_ok=resolved_health,
        )
        return AdoptEvaluation(
            diagnostics=diag,
            candidate=observed_candidate,
            state_snapshot=state_snapshot,
        )

    def try_adopt_all(self, services) -> list[str]:
        """주어진 서비스들에 try_adopt 일괄 시도. 입양 성공한 service_id 리스트 반환."""
        adopted: list[str] = []
        for service in services:
            if self.try_adopt(service) is not None:
                adopted.append(service.id)
        return adopted

    # ------------------------------------------------------------------
    # 외부 인스턴스 종료 (running_external)
    # ------------------------------------------------------------------

    def kill_external(
        self,
        service: ServiceConfig,
        *,
        expected_pid: int | None = None,
        forbidden_pids: set[int] | None = None,
        poll_interval: float = 0.2,
    ) -> dict[str, Any]:
        """포트를 점유한 외부 인스턴스 프로세스를 종료한다.

        SIGINT → SIGTERM → SIGKILL 순서. 자식 정리 핸들러가 있는
        서버에 정리 시간을 준다.

        안전 가드:
        * service.port가 없으면 거부.
        * 컨트롤 서버가 추적 중인 PID(self._states에 등록된 PID)는 외부 인스턴스가
          아니므로 거부 (그 경우 그냥 stop 액션을 써야 함).
        * 컨트롤 서버 자신의 PID도 거부.
        * `forbidden_pids` 인자로 추가 PID를 막을 수 있음.

        반환: {"pid": <죽인 PID>, "signal": "<마지막 시그널>", "duration_seconds": float}.
        """
        if service.port is None:
            raise ProcessError(
                f"{service.id}: port가 설정되어 있지 않아 외부 인스턴스를 식별할 수 없습니다."
            )

        with self._op_lock(service.id):
            return self._kill_external_locked(
                service,
                expected_pid=expected_pid,
                forbidden_pids=forbidden_pids,
                poll_interval=poll_interval,
            )

    def _kill_external_locked(
        self,
        service: ServiceConfig,
        *,
        expected_pid: int | None,
        forbidden_pids: set[int] | None,
        poll_interval: float,
    ) -> dict[str, Any]:
        assert service.port is not None
        holder = _find_port_holder(service.port)
        if holder is None or holder.get("pid") is None:
            raise ProcessError(f"{service.id}: 포트 {service.port}을 점유 중인 프로세스를 찾지 못했습니다.")

        target_pid = int(holder["pid"])
        target_name = holder.get("name", "")
        if expected_pid is not None and target_pid != expected_pid:
            raise ProcessError("외부 종료 대상이 health 확인 뒤 변경되었습니다. 다시 확인하세요.")
        # 안전 가드는 identity 조회보다 먼저 적용한다. 추적 중 PID에는 어떤 추가 OS
        # 관찰이나 signal도 수행하지 않는다.
        if target_pid == os.getpid():
            raise ProcessError("컨트롤 서버 자신을 종료할 수 없습니다.")
        with self._lock:
            tracked_pids = {
                st.pid for st in self._states.values() if st.pid is not None
            }
        if target_pid in tracked_pids:
            raise ProcessError(
                f"PID {target_pid}는 컨트롤 서버가 추적 중인 다른 서비스입니다. "
                "외부 인스턴스 종료가 아니라 해당 서비스의 stop 액션을 사용하세요."
            )
        try:
            target_create_time = psutil.Process(target_pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
            raise ProcessError(f"PID {target_pid}의 신원을 확인할 수 없습니다: {exc}") from exc

        if forbidden_pids and target_pid in forbidden_pids:
            raise ProcessError(f"PID {target_pid}는 종료할 수 없습니다.")

        sequence = [
            (signal.SIGINT, 5.0),
            (signal.SIGTERM, 5.0),
            (signal.SIGKILL, 2.0),
        ]
        started = self._clock()
        last_signal = ""

        for sig, wait in sequence:
            if not _external_identity_current(service.port, target_pid, target_create_time):
                if not last_signal:
                    raise ProcessError("외부 종료 대상이 signal 전 변경되었습니다. 다시 확인하세요.")
                break
            try:
                os.kill(target_pid, sig)
                last_signal = sig.name
            except ProcessLookupError:
                # 이미 죽었음
                break
            except PermissionError as exc:
                raise ProcessError(
                    f"PID {target_pid}에 시그널을 보낼 권한이 없습니다: {exc}"
                ) from exc

            # wait time 동안 polling으로 죽었는지 확인
            deadline = self._clock() + wait
            while self._clock() < deadline:
                if not _external_identity_current(service.port, target_pid, target_create_time):
                    break
                time.sleep(poll_interval)

            if not _external_identity_current(service.port, target_pid, target_create_time):
                break

        # 결과 검증
        if _external_identity_current(service.port, target_pid, target_create_time):
            raise ProcessError(
                f"PID {target_pid} 종료 실패: SIGKILL 후에도 살아있습니다."
            )

        return {
            "pid": target_pid,
            "name": target_name,
            "signal": last_signal,
            "duration_seconds": round(self._clock() - started, 3),
        }

    # ------------------------------------------------------------------
    # 종료 시 정리
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """컨트롤 서버 종료 훅. 추적 중인 자식은 그대로 두고 핸들만 정리한다.

        v1에서는 컨트롤 서버 종료가 곧 자식 종료를 의미하지 않는다.
        always_on 서비스를 운영 중에 컨트롤 서버를 잠깐 재시작할 수 있게 하기 위함.
        """
        with self._lock:
            for sid in list(self._popens.keys()):
                self._popens.pop(sid, None)
            self._log.close_all()


__all__ = [
    "AdoptDiagnostics",
    "AdoptEvaluation",
    "ProcessError",
    "ProcessManager",
    "RuntimeState",
]


# ----------------------------------------------------------------------
# 포트 점유 / PID 검사
#
# 구현은 net_utils로 분리됐다 (process_manager/health_checker 공용). 아래 underscore
# 별칭은 기존 내부 호출부와 테스트 monkeypatch 호환을 위해 유지한다.
# ----------------------------------------------------------------------


def _create_time_matches(proc: psutil.Process, expected: float) -> bool:
    """psutil Process의 create_time이 기대값과 일치하는지 안전하게 본다.

    create_time 호출이 일과성 오류를 던질 수 있으므로 그 경우 True(=일치한다고 가정)로
    돌려 호출자가 즉시 dead로 단정하지 않게 한다.
    """
    try:
        actual = proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return True
    return abs(actual - expected) <= 1.0


def _pid_identity_is_current(state: RuntimeState) -> bool:
    """PID 단위 signal 직전에 tracked identity가 여전히 같은지 엄격히 확인한다.

    관찰 전용 `_is_alive()`와 달리, 여기서는 권한/OS 오류를 alive로 낙관하지 않는다.
    identity를 확인할 수 없는 PID에 signal을 보내는 편보다 명시적으로 중단하는 편이
    안전하다. `False`는 원래 대상의 종료 또는 PID 재사용을 뜻한다.
    """
    if state.pid is None:
        return False
    if state.create_time is None:
        raise ProcessError(f"PID {state.pid}의 create_time이 없어 신원을 확인할 수 없습니다.")
    try:
        proc = psutil.Process(state.pid)
        actual_create_time = proc.create_time()
        running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError) as exc:
        raise ProcessError(f"PID {state.pid}의 신원을 확인할 수 없습니다: {exc}") from exc
    if abs(actual_create_time - state.create_time) > 1.0:
        return False
    return running


def _group_leader_identity_is_current(state: RuntimeState) -> bool:
    """첫 killpg 직전에 leader PID/create-time/PGID가 모두 runtime과 일치하는지 확인한다."""
    if not _pid_identity_is_current(state) or state.pid is None or state.pgid is None:
        return False
    try:
        return os.getpgid(state.pid) == state.pgid
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _external_identity_current(port: int, pid: int, create_time: float) -> bool:
    """외부 종료 대상이 여전히 같은 port holder/PID identity인지 확인한다."""
    holder = _find_port_holder(port)
    if holder is None or holder.get("pid") != pid:
        return False
    try:
        proc = psutil.Process(pid)
        return (
            abs(proc.create_time() - create_time) <= 1.0
            and proc.is_running()
            and proc.status() != psutil.STATUS_ZOMBIE
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def _candidate_identity_is_alive(candidate: _AdoptCandidate) -> bool:
    if candidate.create_time is None:
        return False
    try:
        proc = psutil.Process(candidate.pid)
        return (
            abs(proc.create_time() - candidate.create_time) <= 1.0
            and proc.is_running()
            and proc.status() != psutil.STATUS_ZOMBIE
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def _adopt_backoff_seconds(reason: str) -> float:
    if reason in {"cmdline_mismatch", "cwd_mismatch", "policy_not_manage"}:
        return 10.0
    if reason in {"proc_inspect_failed", "no_port_holder", "health_failed"}:
        return 2.0
    return 0.0


def _adopt_service_signature(service: ServiceConfig) -> tuple[Any, ...]:
    """config reload/change가 이전 관찰 cache를 즉시 무효화하도록 만드는 서명."""
    return (
        service.id,
        service.port,
        service.cwd,
        service.command,
        service.adopt_command,
        service.health.enabled,
        service.health.type,
        service.health.url,
        service.health.timeout_seconds,
        service.health.verify_ssl,
        service.lifecycle.unmanaged_policy,
        service.adopt_match,
    )


def _process_group_is_alive(pgid: int) -> bool:
    """직접 spawn한 process group에 signal을 받을 멤버가 남았는지 확인한다.

    macOS에서 `killpg(..., 0)`은 이미 종료된 group에도 EPERM을 줄 수 있어, 실제
    process 목록에서 같은 PGID의 non-zombie 멤버를 확인한다. 해당 group의 멤버 상태를
    읽지 못하면 stopped로 단정하지 않고 ProcessError로 중단한다.
    """
    try:
        processes = psutil.process_iter(["pid"])
        for proc in processes:
            try:
                if os.getpgid(proc.pid) != pgid:
                    continue
                if proc.status() == psutil.STATUS_ZOMBIE:
                    continue
                return True
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, PermissionError, OSError) as exc:
                # PID가 이미 같은 group이라고 확인된 뒤 상태를 못 읽었으면 보수적으로
                # 중단한다. 다른 프로세스의 접근 거부는 이 group과 무관하므로 건너뛴다.
                try:
                    is_target_group = os.getpgid(proc.pid) == pgid
                except (ProcessLookupError, PermissionError, OSError):
                    is_target_group = False
                if is_target_group:
                    raise ProcessError(
                        f"process group {pgid} 생존 여부를 확인할 수 없습니다: {exc}"
                    ) from exc
    except psutil.Error as exc:
        raise ProcessError(f"process group {pgid} 목록을 확인할 수 없습니다: {exc}") from exc
    return False


def _service_cmdline_match(service: ServiceConfig, actual: list[str]) -> bool:
    """시작 명령, 알려진 exec 래퍼, 명시된 최종 명령과 실제 cmdline을 비교한다."""
    if _cmdline_exact_match(list(service.command), actual):
        return True

    wrapped_command = _tailscale_https_exec_command(service.command)
    if wrapped_command is not None and _cmdline_exact_match(wrapped_command, actual):
        return True

    return (
        service.adopt_command is not None
        and (
            _cmdline_prefix_match(list(service.adopt_command), actual)
            if service.adopt_match == "prefix"
            else _cmdline_exact_match(list(service.adopt_command), actual)
        )
    )


def _tailscale_https_exec_command(command: tuple[str, ...]) -> list[str] | None:
    """with_tailscale_https.sh가 `--` 뒤에서 exec하는 최종 명령을 반환한다."""
    if len(command) < 4:
        return None
    if os.path.basename(command[0]) not in {"bash", "sh"}:
        return None
    if os.path.basename(command[1]) != "with_tailscale_https.sh":
        return None
    try:
        separator = command.index("--", 2)
    except ValueError:
        return None
    wrapped = list(command[separator + 1 :])
    return wrapped or None


def _cmdline_exact_match(expected: list[str], actual: list[str]) -> bool:
    """config의 command와 실제 자식의 cmdline을 비교한다.

    이름 그대로 사실상 exact match다 (예전 이름 `_cmdline_prefix_match`는 오해를 불렀다).

    규칙:
    - 두 cmdline의 길이가 같아야 한다 (인자 누락/추가 차이는 다른 서비스로 본다).
    - 첫 토큰은 basename이 같으면 일치로 본다 (절대경로 vs 짧은 이름 차이 허용).
      예: `python` vs `/opt/anaconda3/bin/python3` → basename `python` vs `python3`라
      basename도 다르므로 불일치. 사용자가 인터프리터 picker를 쓰면 절대경로가 박히고,
      그러면 자식의 cmdline도 같은 절대경로가 들어간다. python vs python3는 다른 명령으로
      간주하는 게 안전.
    - 나머지 토큰은 정확히 일치.

    빈 expected는 항상 False (의도 모호 — 절대 입양 안 함).
    """
    if not expected or not actual or len(expected) != len(actual):
        return False
    head_expected = os.path.basename(expected[0])
    head_actual = os.path.basename(actual[0])
    if head_expected != head_actual and expected[0] != actual[0]:
        return False
    return list(expected[1:]) == list(actual[1:])


def _cmdline_prefix_match(expected: list[str], actual: list[str]) -> bool:
    """명시적 opt-in prefix 매칭: trailing argv만 허용한다."""
    if len(expected) < 2 or len(actual) < len(expected):
        return False
    head_expected = os.path.basename(expected[0])
    head_actual = os.path.basename(actual[0])
    if head_expected != head_actual and expected[0] != actual[0]:
        return False
    return expected[1:] == actual[1 : len(expected)]


def _adopt_health_ok(health) -> bool:
    """입양 후보의 health가 정말 응답하는지 단발성으로 확인.

    health_checker 경로와 무관하게 try_adopt 단독 호출자도 같은 기준을 통과시키기 위함.
    requests를 직접 import해서 의존성 사이클을 만들지 않는다.

    type="http": URL에 GET, 2xx면 OK.
    type="tcp" : url을 host:port로 파싱해 connect 성공이면 OK. Sunshine처럼 모든
                  HTTP API에 인증을 요구하는 서버를 시끄럽게 두드리지 않으려고 추가됐다.
    """
    if health.type == "http":
        if not health.url:
            return False
        try:
            import requests
            response = requests.get(
                health.url,
                timeout=max(1.0, float(health.timeout_seconds)),
                verify=health.verify_ssl,
            )
        except Exception:  # noqa: BLE001
            return False
        return 200 <= response.status_code < 300

    if health.type == "tcp":
        host, port = _parse_tcp_target(health.url)
        if host is None or port is None:
            return False
        return _tcp_probe(host, port, max(0.5, float(health.timeout_seconds)))

    return False


def _command_dir_matches_actual_cwd(
    command: tuple[str, ...] | list[str], actual_cwd_real: str
) -> bool:
    """command[0]이 절대경로 바이너리일 때 그 dirname을 cwd 후보로 본다.

    /opt/homebrew/bin/sunshine처럼 PATH 상의 launcher 바이너리는 실행되면
    스스로 chdir을 하기 때문에 자식 cwd가 config.cwd와 안 맞는다. 그러나
    command 자체에 절대경로가 박혀 있으면 “해당 바이너리 옆 경로”를 다른
    무관한 프로세스로 오인할 위험은 거의 없으므로 입양을 허용해도 안전하다.
    """
    if not command:
        return False
    head = command[0]
    if not head or not os.path.isabs(head):
        return False
    try:
        head_real = os.path.realpath(head)
        head_dir = os.path.dirname(head_real)
    except OSError:
        return False
    if not head_dir:
        return False
    return head_dir == actual_cwd_real


def _resolve_child_path(cwd: Path, value: str) -> str:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)
    return str(cwd / path)


def _can_use_group_kill(state: RuntimeState) -> bool:
    """stop 시 process group 단위 시그널을 보내도 안전한지 판정.

    안전 조건 (둘 중 하나):
    1. 우리가 직접 spawn한 자식 (`adopted=False`) → start_new_session=True로 띄웠으니
       자식이 group leader. 부모의 pgid와 다르고 안에 다른 무관 프로세스가 들어올 일이
       사실상 없다.
    2. (보강) 입양 자식이라도 pgid가 컨트롤 서버 자신의 pgid와 다르고 동시에 자기 자신이
       group leader (pgid == pid)인 경우 → group kill 안전.

    위 둘 다 아니면 (입양 + group leader 아님) PID 단위 kill만 사용해 안전을 우선한다.
    """
    if state.pid is None or state.pgid is None:
        return False
    # 컨트롤 서버 자신의 group을 죽이는 일은 절대 없어야 함.
    try:
        own_pgid = os.getpgrp()
    except OSError:
        return False
    if state.pgid == own_pgid:
        return False
    if not state.adopted:
        # 우리가 spawn한 자식. start_new_session으로 group leader가 됐다.
        return True
    # adopted: 자기 자신이 group leader일 때만 안전.
    return state.pgid == state.pid
