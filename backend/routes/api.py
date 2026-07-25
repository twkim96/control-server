"""서비스 조회/액션/로그 API.

엔드포인트:
* GET  /api/services
* GET  /api/services/<sid>
* POST /api/services/<sid>/actions/<aid>
* GET  /api/services/<sid>/logs
* GET  /api/services/<sid>/logs/stream  (SSE)

GET은 세션 쿠키, mutation은 세션 쿠키 + CSRF 토큰으로 보호한다 (Phase 4).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from auth import auth_required, csrf_required
from config_loader import ServiceConfig
from health_checker import HealthChecker, HealthResult
from log_manager import LogManager
from process_manager import ProcessError, ProcessManager, RuntimeState
from resource_sampler import ResourceSampler
from service_registry import (
    ActionNotFoundError,
    ServiceNotFoundError,
    ServiceRegistry,
)

bp = Blueprint("api", __name__, url_prefix="/api")
_log = logging.getLogger("server_control.api")


def _registry() -> ServiceRegistry:
    return current_app.config["registry"]


def _process_manager() -> ProcessManager:
    return current_app.config["process_manager"]


def _log_manager() -> LogManager:
    return current_app.config["log_manager"]


def _health_checker() -> HealthChecker:
    return current_app.config["health_checker"]


def _resource_sampler() -> ResourceSampler:
    return current_app.config["resource_sampler"]


def _supervisor_diagnostics() -> dict[str, object] | None:
    diagnostics = getattr(_process_manager(), "snapshot_diagnostics", None)
    return diagnostics() if callable(diagnostics) else None


def _build_service_payload(
    service: ServiceConfig,
    *,
    include_runtime: bool = True,
    include_diagnostics: bool = True,
    health_snapshot: dict | None = None,
) -> dict[str, Any]:
    registry = _registry()
    payload = registry.service_to_meta(service)
    if include_runtime:
        result: HealthResult = _health_checker().check(
            service,
            include_port_check=include_diagnostics,
            health_snapshot=health_snapshot,
        )
        resource_state = _resource_state_for(service.id, result)
        payload["runtime"] = {
            "state": result.state,
            "alive": result.alive,
            "unmanaged": result.unmanaged,
            "unmanaged_pid": result.unmanaged_pid,
            "pid": result.pid,
            "pgid": result.pgid,
            "uptime_seconds": result.uptime_seconds,
            "health": result.health,
            "port_check": result.port_check,
            "last_exit_code": result.last_exit_code,
            "last_exit_time": result.last_exit_time,
            "resource": _resource_sampler().sample(
                service.id,
                resource_state,
            ).to_dict(),
        }
        # running_external = 포트는 살아있는데 컨트롤 서버가 추적/입양하지 못한 상태.
        # 왜 자동 입양이 안 됐는지 사유를 실어 보낸다 (상세/펼침 뷰에서만 계산).
        if include_diagnostics and result.state == "running_external":
            evaluation = result.adoption_evaluation
            if evaluation is not None:
                payload["runtime"]["adopt_diagnostics"] = evaluation.diagnostics.to_dict()
    return payload


def _resource_state_for(service_id: str, result: HealthResult) -> RuntimeState:
    if result.pid is not None:
        process_manager = _process_manager()
        get_state = getattr(
            process_manager,
            "get_state_readonly",
            process_manager.get_state,
        )
        return get_state(service_id)
    if result.unmanaged_pid is not None:
        # 외부 인스턴스도 create_time을 채워 PID 재사용 가드를 살린다. 조회 실패 시
        # None으로 두면 sampler가 현재 PID를 그대로 샘플링한다 (기존 동작).
        import psutil

        try:
            create_time = psutil.Process(result.unmanaged_pid).create_time()
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError):
            create_time = None
        return RuntimeState(pid=result.unmanaged_pid, create_time=create_time)
    return RuntimeState()


# ----------------------------------------------------------------------
# 조회
# ----------------------------------------------------------------------


@bp.get("/services")
@auth_required
def list_services():
    services = _registry().list_services()
    # 목록 빌드 전에 헬스 프로브를 병렬로 미리 채운다. 이후 서비스별 순차 check()는
    # 캐시를 재사용하므로 직렬 합(서비스 수 × timeout)이 병렬 최댓값으로 줄어든다.
    health_snapshot = _health_checker().warm_health_snapshot(services)
    payload: dict[str, object] = {
        "services": [
            _build_service_payload(
                s, include_diagnostics=False, health_snapshot=health_snapshot
            )
            for s in services
        ]
    }
    supervisor = _supervisor_diagnostics()
    if supervisor is not None:
        payload["supervisor"] = supervisor
    return jsonify(payload)


@bp.get("/services/<sid>")
@auth_required
def get_service(sid: str):
    try:
        service = _registry().get(sid)
    except ServiceNotFoundError:
        return jsonify({"error": "service_not_found", "id": sid}), 404
    return jsonify(_build_service_payload(service))


# ----------------------------------------------------------------------
# 액션 실행
# ----------------------------------------------------------------------


@bp.post("/services/<sid>/actions/<aid>")
@auth_required
@csrf_required
def trigger_action(sid: str, aid: str):
    registry = _registry()
    try:
        service = registry.get(sid)
        action = registry.get_action(sid, aid)
    except ServiceNotFoundError:
        return jsonify({"error": "service_not_found", "id": sid}), 404
    except ActionNotFoundError:
        return jsonify({"error": "action_not_found", "service": sid, "action": aid}), 404

    if not action.enabled:
        return jsonify({"error": "action_disabled", "action": aid}), 409

    pm = _process_manager()

    try:
        if action.type == "process_start":
            state = pm.start(service)
            _log.info("start: %s pid=%s", sid, state.pid)
        elif action.type == "process_stop":
            if action.stop_strategy is None:
                return jsonify({"error": "stop_strategy_missing"}), 500
            pm.stop(service, action.stop_strategy)
            _log.info("stop: %s", sid)
        elif action.type == "process_restart":
            stop_action = next(
                (a for a in service.actions if a.type == "process_stop" and a.stop_strategy),
                None,
            )
            strategy = stop_action.stop_strategy if stop_action else None
            if strategy is None:
                return jsonify({"error": "stop_strategy_missing_for_restart"}), 500
            state = pm.restart(service, strategy)
            _log.info("restart: %s pid=%s", sid, state.pid)
        elif action.type == "health_check":
            # 실제 체크는 응답에 포함된 runtime payload에서 수행됨.
            pass
        elif action.type in {"show_logs", "open_url", "edit_config"}:
            # 서버측에서 별도 작업 없음. UI가 알아서 처리.
            pass
        else:
            return jsonify({"error": "unsupported_action_type", "type": action.type}), 400
    except ProcessError as exc:
        return jsonify({"error": "process_error", "message": str(exc)}), 409

    return jsonify(
        {
            "ok": True,
            "service": _build_service_payload(service),
        }
    )


@bp.post("/services/<sid>/kill_external")
@auth_required
@csrf_required
def kill_external(sid: str):
    """포트를 점유한 외부 인스턴스 프로세스를 종료한다.

    안전 절차:
    1) 서비스가 health URL을 켜놓은 경우에 한해 동작 (단순 포트 점유만으로는 거부).
    2) 호출 직전 health URL을 한 번 더 확인. running_external 상태가 아니면 거부.
       UI가 stale인 채로 호출되거나, 다른 프로세스가 그새 포트를 잡은 경우를 막는다.
    3) process_manager.kill_external 안의 PID 안전 가드(자기 자신/추적 PID 거부).
    """
    registry = _registry()
    try:
        service = registry.get(sid)
    except ServiceNotFoundError:
        return jsonify({"error": "service_not_found", "id": sid}), 404

    # 1) health 비활성이거나 지원하지 않는 방식이면 외부 인스턴스 식별 자체가 불가.
    if (
        not service.health.enabled
        or service.health.type not in {"http", "tcp"}
        or not service.health.url
    ):
        return (
            jsonify(
                {
                    "error": "kill_external_requires_health",
                    "message": (
                        "외부 인스턴스 종료는 HTTP 또는 TCP health check가 "
                        "활성화된 서비스에만 사용할 수 있습니다."
                    ),
                }
            ),
            409,
        )

    # 2) 직전 상태가 running_external인지 다시 확인.
    health = _health_checker().check(
        service,
        include_port_check=False,
        force_health_probe=True,
    )
    if health.state != "running_external":
        return (
            jsonify(
                {
                    "error": "not_running_external",
                    "message": (
                        f"서비스가 외부 인스턴스 상태가 아닙니다 (state={health.state}). "
                        "외부 인스턴스만 이 액션으로 종료할 수 있습니다."
                    ),
                    "state": health.state,
                }
            ),
            409,
        )

    pm = _process_manager()
    try:
        result = pm.kill_external(service, expected_pid=health.unmanaged_pid)
    except ProcessError as exc:
        return jsonify({"error": "kill_external_failed", "message": str(exc)}), 409

    _log.info(
        "kill_external: %s pid=%s signal=%s duration=%s",
        sid,
        result["pid"],
        result["signal"],
        result["duration_seconds"],
    )
    return jsonify(
        {
            "ok": True,
            "killed": result,
            "service": _build_service_payload(service),
        }
    )


# ----------------------------------------------------------------------
# 로그
# ----------------------------------------------------------------------


@bp.get("/services/<sid>/logs")
@auth_required
def read_logs(sid: str):
    registry = _registry()
    try:
        service = registry.get(sid)
    except ServiceNotFoundError:
        return jsonify({"error": "service_not_found", "id": sid}), 404

    since = request.args.get("since_offset", type=int)
    lm = _log_manager()
    if since is not None:
        lines, offset = lm.read_since(sid, since)
    else:
        n = request.args.get("tail", type=int) or service.log.tail_lines
        lines, offset = lm.tail(sid, n)
    return jsonify({"lines": lines, "offset": offset})


@bp.get("/services/<sid>/logs/stream")
@auth_required
def stream_logs(sid: str):
    registry = _registry()
    try:
        registry.get(sid)
    except ServiceNotFoundError:
        return jsonify({"error": "service_not_found", "id": sid}), 404

    lm = _log_manager()
    stop_event = threading.Event()

    @stream_with_context
    def generate():
        try:
            # 초기 tail 한 번 보내서 새로 들어오는 클라이언트도 컨텍스트를 갖게 한다.
            tail, _offset = lm.tail(sid, registry.get(sid).log.tail_lines)
            for line in tail:
                yield _sse_event("line", line)
            yield _sse_event("ready", "")

            for item in lm.stream(sid, from_start=False, stop_event=stop_event):
                if item is None:
                    # keepalive comment. EventSource는 ":"로 시작하는 줄을 무시한다.
                    yield ": ping\n\n"
                else:
                    yield _sse_event("line", item)
        except GeneratorExit:
            stop_event.set()
            raise

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # 프록시가 있을 때 버퍼링 방지
    }
    return Response(generate(), mimetype="text/event-stream", headers=headers)


def _sse_event(event: str, data: str) -> str:
    payload = json.dumps({"data": data}, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


__all__ = ["bp"]
