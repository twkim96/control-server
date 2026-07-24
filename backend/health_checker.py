"""서비스 health 상태 계산.

전체 상태(state)를 다음 7개 중 하나로 분류한다:
* running           : PID 살아있고 (health 비활성 OR health 통과)
* running_external  : 컨트롤 서버가 띄우지 않았지만 health URL이 정상 응답
                      (외부 터미널/다른 launchd로 떠 있는 인스턴스)
* unhealthy         : PID 살아있지만 health 실패
* stopped           : 추적 중인 PID 없음 + health도 응답 없음
* starting          : start_time 직후 (PID 살아있고 아직 health 첫 응답 전)
* stopping          : 중지 액션 진행 중 (호출자가 외부에서 마킹할 때만 사용)
* unknown           : 위에 해당 안 되는 fallback

상태 판정 규칙:
* PID 살아있으면 우선 살아있음 인정.
* health URL이 있으면 HTTP 요청으로 추가 검증.
* 포트는 보조 정보. health 실패 시 포함하면 디버깅에 유리.
* `lifecycle.unmanaged_policy: status_only`일 때만 외부 인스턴스를 인식한다.
"""

from __future__ import annotations

import socket
import ssl
import threading
import time
import urllib3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import requests

from config_loader import HealthConfig, ServiceConfig
from process_manager import AdoptEvaluation, ProcessManager


# verify_ssl=False일 때 나오는 InsecureRequestWarning을 한 번만 죽인다.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# starting 상태로 간주할 시간 (초). 이 시간 안에 health 실패하더라도
# unhealthy로 단정하지 않고 starting을 유지한다.
STARTING_GRACE_SECONDS = 3.0


@dataclass(frozen=True)
class HealthResult:
    state: str
    alive: bool
    unmanaged: bool  # True면 외부 인스턴스가 health에 응답 중
    unmanaged_pid: int | None  # 외부 인스턴스의 PID (있으면)
    pid: int | None
    pgid: int | None
    uptime_seconds: float | None
    health: dict[str, Any]
    port_check: dict[str, Any] | None
    last_exit_code: int | None
    last_exit_time: float | None
    adoption_evaluation: AdoptEvaluation | None = None


class HealthChecker:
    def __init__(
        self,
        process_manager: ProcessManager,
        *,
        clock=time.time,
        monotonic_clock=None,
        health_ttl_seconds: float = 2.0,
    ) -> None:
        self._pm = process_manager
        self._clock = clock
        self._monotonic_clock = time.monotonic if monotonic_clock is None else monotonic_clock
        # 헬스 프로브 결과 캐시 TTL (초). 0이면 캐시 비활성.
        # 여러 클라이언트/탭이 동시에 폴링하거나 목록+상세가 겹칠 때 같은 health URL을
        # 중복으로 두드리지 않게 한다. 부하 관점에서 ResourceSampler와 같은 전략.
        self._health_ttl = max(0.0, float(health_ttl_seconds))
        # key=(type, url, verify_ssl, timeout_seconds) -> (sampled_at, result dict)
        self._probe_cache: dict[tuple[str, str | None, bool, float], tuple[float, dict[str, Any]]] = {}
        self._probe_inflight: dict[tuple[str, str | None, bool, float], threading.Event] = {}
        self._probe_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="health")
        verified_context = ssl.create_default_context(cafile=requests.certs.where())
        # PoolManager is thread-safe. Reusing one SSLContext also avoids loading the
        # CA bundle again whenever a health server closes an idle connection.
        self._verified_http = urllib3.PoolManager(
            num_pools=16,
            maxsize=4,
            ssl_context=verified_context,
        )
        self._insecure_http = urllib3.PoolManager(
            num_pools=16,
            maxsize=4,
            cert_reqs=ssl.CERT_NONE,
        )

    def check(
        self,
        service: ServiceConfig,
        *,
        include_port_check: bool = True,
        force_health_probe: bool = False,
        health_snapshot: dict[tuple[str, str | None, bool, float], dict[str, Any]] | None = None,
    ) -> HealthResult:
        state, alive = self._pm.inspect_state(service.id)

        # 추적 중인 PID가 없는데 외부에 health URL이 응답하고 있고, 정책이 manage라면
        # 입양을 시도한다 (v1.2.5). status_only/manage 둘 다 health URL은 한 번 묻는다.
        external_eligible = (
            not alive
            and service.health.enabled
            and service.lifecycle.unmanaged_policy in ("status_only", "manage")
        )
        if alive or external_eligible:
            if force_health_probe:
                health_info = self._http_health(service.health)
            elif health_snapshot is not None and self._probe_cache_key(service.health) in health_snapshot:
                health_info = health_snapshot[self._probe_cache_key(service.health)]
            else:
                health_info = self._probe_health(service.health)
        else:
            health_info = _health_skipped()

        adoption_evaluation: AdoptEvaluation | None = None
        # 외부 health OK인 경우 한 번의 evaluation을 입양/상태/진단에서 공유한다.
        if not alive and external_eligible and health_info.get("ok") is True:
            adoption_evaluation = self._pm.evaluate_adopt(
                service, health_ok=health_info.get("ok") is True
            )

        # manage 정책 + 외부 health OK → 입양 시도.
        if (
            not alive
            and external_eligible
            and service.lifecycle.unmanaged_policy == "manage"
            and health_info.get("ok") is True
            and getattr(self._pm, "supports_adoption", True)
        ):
            adopted = self._pm.try_adopt(service, evaluation=adoption_evaluation)
            if adopted is not None:
                # 입양 성공. alive로 다시 산출하고 phase 결정.
                state = self._pm.get_state(service.id)
                alive = self._pm.is_alive(service.id)

        uptime = None
        if alive and state.start_time is not None:
            uptime = max(0.0, self._clock() - state.start_time)

        unmanaged = False
        unmanaged_pid: int | None = None
        if alive:
            in_grace = uptime is not None and uptime < STARTING_GRACE_SECONDS
            if not service.health.enabled:
                phase = "running"
            elif health_info.get("ok") is True:
                phase = "running"
            elif in_grace:
                phase = "starting"
            else:
                phase = "unhealthy"
        elif external_eligible and health_info.get("ok") is True:
            # 외부 인스턴스가 답하고 있다 (입양 실패했거나 status_only).
            phase = "running_external"
            unmanaged = True
            # 포트를 점유한 프로세스 PID를 알아낸다.
            if adoption_evaluation is not None:
                unmanaged_pid = adoption_evaluation.diagnostics.candidate_pid
        else:
            never_started = (
                state.pid is None
                and state.last_exit_time is None
                and not state.extra.get("pm2_status")
            )
            phase = "unknown" if never_started else "stopped"

        port_check = None
        if include_port_check and service.port is not None:
            # alive인데 health 실패(원인 디버깅) 또는
            # 죽었는데 health 응답 없음(외부 인스턴스 부재 확인)일 때 포트 점유 정보 포함.
            health_failed_while_alive = (
                alive and service.health.enabled and health_info.get("ok") is False
            )
            stopped_with_health_eligible = (
                external_eligible and health_info.get("ok") is not True
            )
            if health_failed_while_alive or stopped_with_health_eligible:
                port_check = self._port_check(service.port)

        return HealthResult(
            state=phase,
            alive=alive,
            unmanaged=unmanaged,
            unmanaged_pid=unmanaged_pid,
            pid=state.pid,
            pgid=state.pgid,
            uptime_seconds=uptime,
            health=health_info,
            port_check=port_check,
            last_exit_code=state.last_exit_code,
            last_exit_time=state.last_exit_time,
            adoption_evaluation=adoption_evaluation,
        )

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _probe_cache_key(self, cfg: HealthConfig) -> tuple[str, str | None, bool, float]:
        return (cfg.type, cfg.url, cfg.verify_ssl, float(cfg.timeout_seconds))

    def _probe_health(self, cfg: HealthConfig) -> dict[str, Any]:
        """헬스 프로브 결과를 TTL 캐시로 감싼 진입점.

        같은 health 타깃(type+url+verify_ssl)에 대한 프로브 결과를 짧게 재사용한다.
        check()가 매 폴링마다 호출되고, 목록+상세+여러 탭이 겹칠 수 있으므로 캐시가
        없으면 그 수만큼 같은 URL을 두드리게 된다.
        """
        if not cfg.enabled:
            return self._http_health(cfg)
        if self._health_ttl <= 0:
            return self._http_health(cfg)

        key = self._probe_cache_key(cfg)
        while True:
            now = self._monotonic_clock()
            with self._probe_lock:
                entry = self._probe_cache.get(key)
                if entry is not None and now - entry[0] < self._health_ttl:
                    return entry[1]
                event = self._probe_inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._probe_inflight[key] = event
                    owner = True
                else:
                    owner = False

            if not owner:
                event.wait()
                # owner가 예외로 끝나 cache를 못 채운 경우에는 다음 호출자가 다시 owner가 된다.
                continue

            try:
                result = self._http_health(cfg)
            except BaseException:
                with self._probe_lock:
                    self._probe_inflight.pop(key, None)
                    event.set()
                raise
            with self._probe_lock:
                self._probe_cache[key] = (self._monotonic_clock(), result)
                self._probe_inflight.pop(key, None)
                event.set()
            return result

    def warm_health_snapshot(
        self, services
    ) -> dict[tuple[str, str | None, bool, float], dict[str, Any]]:
        """여러 서비스의 헬스 프로브를 병렬로 미리 채운다.

        `/api/services` 목록은 서비스마다 check()를 순차 호출하는데, 각 check()가
        동기 HTTP 헬스를 직렬로 기다리면 한 응답 시간이 sum(timeout)까지 늘어진다.
        목록 빌드 전에 이 메서드로 캐시를 병렬 prewarm하면, 이후 순차 check()는 캐시를
        재사용하므로 직렬 합이 병렬 최댓값으로 줄어든다.

        이 경로는 순수 네트워크 프로브(_probe_health)만 병렬화한다. ProcessManager 락이나
        Flask current_app을 건드리지 않아 워커 스레드에서 안전하다.
        """
        targets: dict[tuple[str, str | None, bool, float], HealthConfig] = {}
        for service in services:
            cfg = service.health
            if cfg.enabled:
                targets.setdefault(self._probe_cache_key(cfg), cfg)

        if not targets:
            return {}
        futures = {key: self._executor.submit(self._probe_health, cfg) for key, cfg in targets.items()}
        return {key: future.result() for key, future in futures.items()}

    def warm_health_cache(self, services, *, max_workers: int = 8) -> None:
        """하위 호환용 wrapper. worker 수는 전역 executor의 8개로 고정한다."""
        del max_workers
        self.warm_health_snapshot(services)

    def close(self) -> None:
        """전역 health worker를 종료한다. 앱 shutdown/tests에서 호출 가능."""
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _http_health(self, cfg: HealthConfig) -> dict[str, Any]:
        if not cfg.enabled:
            return {"enabled": False, "ok": None}
        if cfg.type == "tcp":
            return self._tcp_health(cfg)
        if cfg.type != "http" or not cfg.url:
            return {"enabled": True, "ok": None, "reason": "unsupported health type"}

        started = self._clock()
        try:
            pool = self._verified_http if cfg.verify_ssl else self._insecure_http
            response = pool.request(
                "GET",
                cfg.url,
                timeout=urllib3.Timeout(
                    connect=cfg.timeout_seconds,
                    read=cfg.timeout_seconds,
                ),
                retries=False,
            )
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            return {
                "enabled": True,
                "ok": False,
                "reason": f"request_failed: {exc.__class__.__name__}",
                "url": cfg.url,
            }
        elapsed = self._clock() - started

        ok = 200 <= response.status < 300
        return {
            "enabled": True,
            "ok": ok,
            "url": cfg.url,
            "status_code": response.status,
            "elapsed_seconds": round(elapsed, 3),
        }

    def _tcp_health(self, cfg: HealthConfig) -> dict[str, Any]:
        """`type: tcp` health. host:port에 connect만 시도한다.

        Sunshine처럼 모든 HTTP API에 인증을 요구해 헤더 없는 GET이 401을 뱉으며
        서버 로그를 더럽히는 케이스를 위해 추가됐다. 응답 본문은 보지 않으니
        애플리케이션 레벨 헬스는 보장하지 못하지만, "포트가 LISTEN 중"이라는
        사실만으로 alive를 판단하기엔 충분하다.
        """
        from net_utils import parse_tcp_target, tcp_probe

        host, port = parse_tcp_target(cfg.url)
        if host is None or port is None:
            return {
                "enabled": True,
                "ok": False,
                "reason": "invalid tcp target",
                "url": cfg.url,
            }
        started = self._clock()
        ok = tcp_probe(host, port, max(0.5, float(cfg.timeout_seconds)))
        elapsed = self._clock() - started
        return {
            "enabled": True,
            "ok": ok,
            "url": cfg.url,
            "elapsed_seconds": round(elapsed, 3),
        }

    def _port_check(self, port: int) -> dict[str, Any]:
        # 127.0.0.1로만 확인. 외부 인터페이스는 v1 범위 밖.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            result = sock.connect_ex(("127.0.0.1", port))
        finally:
            sock.close()
        return {"port": port, "open": result == 0, "errno": result if result else None}


def _health_skipped() -> dict[str, Any]:
    return {"enabled": False, "ok": None, "reason": "process_not_alive"}


__all__ = ["HealthChecker", "HealthResult", "STARTING_GRACE_SECONDS"]
