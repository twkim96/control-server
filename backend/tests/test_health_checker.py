"""health_checker의 state 분류 단위 테스트 (running_external 포함)."""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from config_loader import (
    HealthConfig,
    LifecycleConfig,
    LogConfig,
    ServiceConfig,
)
from health_checker import HealthChecker
from log_manager import LogManager
from process_manager import ProcessManager


def _make_service(
    *,
    health_enabled: bool = True,
    health_url: str = "http://127.0.0.1:1/health",
    unmanaged_policy: str = "status_only",
    port: int | None = 1,
) -> ServiceConfig:
    return ServiceConfig(
        id="svc",
        name="Svc",
        description="",
        cwd="/tmp",
        entry_file="x.py",
        command=("python", "x.py"),
        env={},
        port=port,
        port_env_name=None,
        open_url=None,
        health=HealthConfig(
            enabled=health_enabled,
            type="http" if health_enabled else "none",
            url=health_url if health_enabled else None,
            timeout_seconds=2.0,
            verify_ssl=True,
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


@pytest.fixture
def pm(tmp_path: Path) -> ProcessManager:
    lm = LogManager(tmp_path / "logs")
    return ProcessManager(tmp_path / "runtime", lm)


def _stub_health_response(ok: bool, status_code: int = 200):
    """PoolManager.request를 흉내내는 stub."""
    def fake_request(method, url, timeout=None, retries=False):
        if not ok:
            import urllib3 as _u
            raise _u.exceptions.HTTPError("stub: refused")

        class _Resp:
            def __init__(self, code: int):
                self.status = code

        return _Resp(status_code)

    return fake_request


def test_no_pid_no_health_returns_unknown_then_stopped(pm):
    """PID도 없고 health도 응답 안 함 → never_started면 unknown, 한 번 stop된 적 있으면 stopped."""
    checker = HealthChecker(pm)
    service = _make_service()
    with patch.object(
        checker._verified_http,
        "request",
        side_effect=_stub_health_response(ok=False),
    ):
        result = checker.check(service)
    # 처음이면 unknown
    assert result.state in ("unknown", "stopped")
    assert result.alive is False
    assert result.unmanaged is False


def test_external_instance_detected_as_running_external(pm):
    """PID 없음 + health 200 + unmanaged_policy=status_only → running_external."""
    checker = HealthChecker(pm)
    service = _make_service()
    with patch.object(
        checker._verified_http,
        "request",
        side_effect=_stub_health_response(ok=True),
    ):
        result = checker.check(service)
    assert result.state == "running_external"
    assert result.alive is False
    assert result.unmanaged is True
    assert result.pid is None
    # unmanaged_pid는 None일 수도 있고 (port=1은 보통 비어있음) 실제 PID일 수도 있음.
    # port에 실제 listener가 있으면 PID 식별. 테스트 환경에선 None.
    assert result.unmanaged_pid is None or isinstance(result.unmanaged_pid, int)
    assert result.health["ok"] is True


def test_external_instance_with_unmanaged_policy_disabled(pm):
    """unmanaged_policy가 status_only/manage가 아니면 health URL 시도 안 함.

    v1.2.5부터 status_only와 manage 둘 다 health 체크는 한다 (manage는 추가로 입양 시도).
    이 테스트는 둘 다 아닌(예전에 의도되었던) 정책에서 health가 호출 안 되는지 잠근다.
    config_loader가 manage/status_only만 허용하므로 실질적으로 도달 어려운 케이스지만
    가드 자체를 잠그는 의미.
    """
    checker = HealthChecker(pm)
    # config_loader는 'unmanaged'를 허용하지 않으므로 직접 dataclass를 만들어 우회.
    from config_loader import (
        HealthConfig,
        LifecycleConfig,
        LogConfig,
        ServiceConfig,
    )
    bypass_service = ServiceConfig(
        id="bypass",
        name="bypass",
        description="",
        cwd="/tmp",
        entry_file="x.py",
        command=("python", "x.py"),
        env={},
        port=12345,
        port_env_name=None,
        open_url=None,
        health=HealthConfig(
            enabled=True, type="http", url="http://127.0.0.1:12345/health",
            timeout_seconds=2.0, verify_ssl=True,
        ),
        log=LogConfig(enabled=True, tail_lines=200, max_bytes=0, keep=0),
        lifecycle=LifecycleConfig(
            mode="manual",
            autostart=False,
            stop_visibility="primary",
            restart_visibility="primary",
            unmanaged_policy="other",  # type: ignore[arg-type]
        ),
        actions=(),
    )
    with patch.object(checker._verified_http, "request") as mock_get:
        result = checker.check(bypass_service)
    # health URL이 호출되지 않아야 함
    assert mock_get.call_count == 0
    assert result.state in ("unknown", "stopped")
    assert result.unmanaged is False


def test_external_instance_with_health_disabled(pm):
    """health.enabled=False면 외부 인스턴스 감지 안 함."""
    checker = HealthChecker(pm)
    service = _make_service(health_enabled=False)
    with patch.object(checker._verified_http, "request") as mock_get:
        result = checker.check(service)
    assert mock_get.call_count == 0
    assert result.state in ("unknown", "stopped")
    assert result.unmanaged is False


def test_external_instance_health_failed_falls_back_to_stopped(pm):
    """unmanaged 정책이지만 health 응답이 없으면 stopped/unknown."""
    checker = HealthChecker(pm)
    service = _make_service()
    with patch.object(
        checker._verified_http,
        "request",
        side_effect=_stub_health_response(ok=False),
    ):
        result = checker.check(service)
    assert result.state != "running_external"
    assert result.unmanaged is False


def test_http_health_reuses_one_connection_pool_across_checks(pm):
    service = _make_service()
    # 캐시를 끄고 풀 재사용(매 check마다 실제 request) 의도만 검증한다.
    checker = HealthChecker(pm, health_ttl_seconds=0.0)
    with patch.object(
        checker._verified_http,
        "request",
        side_effect=_stub_health_response(ok=False),
    ) as request:
        checker.check(service)
        checker.check(service)

    assert request.call_count == 2


def test_http_health_uses_insecure_pool_when_ssl_verification_is_disabled(pm):
    service = replace(
        _make_service(),
        health=HealthConfig(
            enabled=True,
            type="http",
            url="https://127.0.0.1:1/health",
            timeout_seconds=2.0,
            verify_ssl=False,
        ),
    )
    checker = HealthChecker(pm)
    with patch.object(
        checker._insecure_http,
        "request",
        side_effect=_stub_health_response(ok=False),
    ) as request:
        checker.check(service)

    request.assert_called_once()


def test_running_service_checks_pid_once_per_health_pass(pm):
    service = _make_service(health_enabled=False)
    state = pm.get_state(service.id)
    state.pid = 123

    checker = HealthChecker(pm)
    with patch.object(pm, "_is_alive", return_value=True) as is_alive:
        result = checker.check(service)

    assert result.alive is True
    is_alive.assert_called_once_with(state)


# ----------------------------------------------------------------------
# v1.3.3: 헬스 프로브 TTL 캐시 + 병렬 prewarm
# ----------------------------------------------------------------------


def test_probe_cache_reuses_result_within_ttl(pm):
    """TTL 안에서는 같은 health URL을 다시 두드리지 않는다."""
    service = _make_service()
    clock = {"t": 1000.0}
    checker = HealthChecker(
        pm, clock=lambda: clock["t"], monotonic_clock=lambda: clock["t"], health_ttl_seconds=2.0
    )

    with patch.object(
        checker._verified_http,
        "request",
        side_effect=_stub_health_response(ok=True),
    ) as request:
        checker._probe_health(service.health)
        clock["t"] += 1.0  # TTL(2.0) 이내
        checker._probe_health(service.health)

    assert request.call_count == 1


def test_probe_cache_reprobes_after_ttl(pm):
    """TTL이 지나면 다시 프로브한다."""
    service = _make_service()
    clock = {"t": 1000.0}
    checker = HealthChecker(
        pm, clock=lambda: clock["t"], monotonic_clock=lambda: clock["t"], health_ttl_seconds=2.0
    )

    with patch.object(
        checker._verified_http,
        "request",
        side_effect=_stub_health_response(ok=True),
    ) as request:
        checker._probe_health(service.health)
        clock["t"] += 3.0  # TTL(2.0) 초과
        checker._probe_health(service.health)

    assert request.call_count == 2


def test_probe_cache_disabled_when_ttl_zero(pm):
    """ttl=0이면 매번 프로브한다."""
    service = _make_service()
    checker = HealthChecker(pm, health_ttl_seconds=0.0)

    with patch.object(
        checker._verified_http,
        "request",
        side_effect=_stub_health_response(ok=True),
    ) as request:
        checker._probe_health(service.health)
        checker._probe_health(service.health)

    assert request.call_count == 2


def test_warm_health_cache_populates_so_check_reuses(pm):
    """prewarm 후 check()는 캐시를 재사용해 추가 프로브를 하지 않는다."""
    service = _make_service()
    checker = HealthChecker(pm, health_ttl_seconds=5.0)

    with patch.object(
        checker._verified_http,
        "request",
        side_effect=_stub_health_response(ok=True),
    ) as request:
        checker.warm_health_cache([service])
        assert request.call_count == 1
        # check()는 캐시된 프로브를 그대로 사용 (외부 인스턴스 경로에서도 재프로브 없음)
        with patch.object(pm, "inspect_state", return_value=(pm.get_state(service.id), False)):
            checker.check(service, include_port_check=False)
        assert request.call_count == 1


def test_warm_health_cache_dedupes_shared_url(pm):
    """같은 health URL을 쓰는 여러 서비스는 prewarm에서 한 번만 프로브한다."""
    s1 = replace(_make_service(), id="a")
    s2 = replace(_make_service(), id="b")  # 같은 health url
    checker = HealthChecker(pm, health_ttl_seconds=5.0)

    with patch.object(
        checker._verified_http,
        "request",
        side_effect=_stub_health_response(ok=True),
    ) as request:
        checker.warm_health_cache([s1, s2])

    assert request.call_count == 1


def test_warm_health_cache_skips_disabled_health(pm):
    """health 비활성 서비스는 prewarm 대상이 아니다."""
    service = _make_service(health_enabled=False)
    checker = HealthChecker(pm, health_ttl_seconds=5.0)

    with patch.object(
        checker._verified_http,
        "request",
        side_effect=_stub_health_response(ok=True),
    ) as request:
        checker.warm_health_cache([service])

    assert request.call_count == 0


def test_probe_single_flight_coalesces_concurrent_cold_calls(pm):
    """같은 health key의 동시 cold miss는 실제 probe 하나만 수행한다."""
    service = _make_service()
    checker = HealthChecker(pm, health_ttl_seconds=5.0)
    barrier = threading.Barrier(8)
    calls = 0
    calls_lock = threading.Lock()
    results: list[dict] = []

    def probe(_cfg):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.1)
        return {"enabled": True, "ok": True}

    with patch.object(checker, "_http_health", side_effect=probe):
        threads = [
            threading.Thread(
                target=lambda: (barrier.wait(timeout=5), results.append(checker._probe_health(service.health)))
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert calls == 1
    assert results == [{"enabled": True, "ok": True}] * 8


def test_probe_cache_key_includes_timeout(pm):
    """timeout 차이가 결과 의미를 바꾸므로 cache key도 달라야 한다."""
    first = _make_service()
    second = replace(
        first,
        health=replace(first.health, timeout_seconds=3.0),
    )
    checker = HealthChecker(pm, health_ttl_seconds=5.0)
    with patch.object(checker, "_http_health", return_value={"enabled": True, "ok": True}) as probe:
        checker._probe_health(first.health)
        checker._probe_health(second.health)
    assert probe.call_count == 2


def test_request_snapshot_prevents_reprobe_after_ttl(pm):
    """같은 목록 요청의 snapshot은 TTL이 지나도 payload check에서 재사용한다."""
    service = _make_service()
    clock = {"t": 1000.0}
    checker = HealthChecker(
        pm,
        clock=lambda: clock["t"],
        monotonic_clock=lambda: clock["t"],
        health_ttl_seconds=1.0,
    )
    try:
        with patch.object(checker, "_http_health", return_value={"enabled": True, "ok": True}) as probe:
            snapshot = checker.warm_health_snapshot([service])
            clock["t"] += 2.0
            with patch.object(pm, "inspect_state", return_value=(pm.get_state(service.id), False)):
                checker.check(service, include_port_check=False, health_snapshot=snapshot)
        assert probe.call_count == 1
    finally:
        checker.close()
