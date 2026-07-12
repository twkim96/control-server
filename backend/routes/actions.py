"""ActionGroup / ActionItem / ActionRun API.

엔드포인트:
* GET    /api/actions
* POST   /api/actions/<group_id>/<item_id>/run
* GET    /api/actions/runs/<run_id>
* GET    /api/actions/runs/<run_id>/stream  (SSE)
* POST   /api/actions/runs/<run_id>/cancel
* GET    /api/actions/external_log         (?path=...)
* GET    /api/actions/external_log/stream  (?path=...) (SSE)

GET은 세션 쿠키, mutation은 세션 쿠키 + CSRF 토큰으로 보호한다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from action_runner import ActionRunError, ActionRunner
from auth import auth_required, csrf_required
from file_browser import FileBrowser
from run_log_manager import RunLogManager
from service_registry import (
    ActionGroupNotFoundError,
    ActionItemNotFoundError,
    ServiceRegistry,
)

bp = Blueprint("actions_api", __name__, url_prefix="/api/actions")
_log = logging.getLogger("server_control.actions_api")

RUN_LOG_TAIL_DEFAULT = 200
RUN_LOG_TAIL_MAX = 5000


def _registry() -> ServiceRegistry:
    return current_app.config["registry"]


def _runner() -> ActionRunner:
    return current_app.config["action_runner"]


def _run_log() -> RunLogManager:
    return current_app.config["run_log_manager"]


def _file_browser() -> FileBrowser:
    return current_app.config["file_browser"]


def _run_to_payload(run) -> dict[str, Any]:
    return run.to_dict()


def _runs_for_item(group_id: str, item_id: str, limit: int = 10) -> list[dict[str, Any]]:
    runs = _runner().list_runs(group_id, item_id)
    runs.sort(key=lambda r: r.started_at, reverse=True)
    return [r.to_dict() for r in runs[:limit]]


# ----------------------------------------------------------------------
# 조회
# ----------------------------------------------------------------------


@bp.get("")
@auth_required
def list_actions():
    registry = _registry()
    groups = []
    for group in registry.list_action_groups():
        meta = registry.action_group_to_meta(group)
        # 각 ActionItem의 최근 run 메타데이터를 함께 내려준다.
        for item_meta in meta["items"]:
            item_meta["recent_runs"] = _runs_for_item(group.id, item_meta["id"])
        groups.append(meta)
    return jsonify({"groups": groups})


@bp.get("/runs/<run_id>")
@auth_required
def get_run(run_id: str):
    run = _runner().get_run(run_id)
    if run is None:
        return jsonify({"error": "run_not_found", "run_id": run_id}), 404

    requested_tail = request.args.get("tail", type=int)
    if requested_tail is None:
        requested_tail = RUN_LOG_TAIL_DEFAULT
    tail_lines = min(max(requested_tail, 0), RUN_LOG_TAIL_MAX)
    rl = _run_log()
    lines, offset = rl.tail(run.group_id, run.item_id, run.run_id, tail_lines)
    return jsonify(
        {
            "run": _run_to_payload(run),
            "lines": lines,
            "offset": offset,
        }
    )


# ----------------------------------------------------------------------
# 실행 / 중지
# ----------------------------------------------------------------------


@bp.post("/<group_id>/<item_id>/run")
@auth_required
@csrf_required
def start_run(group_id: str, item_id: str):
    registry = _registry()
    try:
        group = registry.get_action_group(group_id)
        item = registry.get_action_item(group_id, item_id)
    except ActionGroupNotFoundError:
        return jsonify({"error": "action_group_not_found", "id": group_id}), 404
    except ActionItemNotFoundError:
        return jsonify({"error": "action_item_not_found", "id": item_id}), 404

    # python kind는 cwd가 allowed_path_roots 안에 있어야 함 (실행 시점 재검증).
    if item.kind == "python" and item.cwd:
        if not _file_browser().is_allowed(item.cwd):
            return (
                jsonify(
                    {
                        "error": "cwd_not_allowed",
                        "message": f"working directory가 허용 루트 밖에 있습니다: {item.cwd}",
                    }
                ),
                403,
            )

    try:
        run = _runner().start(group, item)
    except ActionRunError as exc:
        return jsonify({"error": "action_run_failed", "message": str(exc)}), 409

    _log.info("run started: %s/%s run_id=%s", group_id, item_id, run.run_id)
    return jsonify({"ok": True, "run": _run_to_payload(run)})


@bp.post("/runs/<run_id>/cancel")
@auth_required
@csrf_required
def cancel_run(run_id: str):
    runner = _runner()
    run = runner.get_run(run_id)
    if run is None:
        return jsonify({"error": "run_not_found", "run_id": run_id}), 404

    try:
        run = runner.cancel(run_id)
    except ActionRunError as exc:
        return jsonify({"error": "cancel_failed", "message": str(exc)}), 409

    _log.info("run cancelled: %s", run_id)
    return jsonify({"ok": True, "run": _run_to_payload(run)})


# ----------------------------------------------------------------------
# 로그 스트림
# ----------------------------------------------------------------------


@bp.get("/runs/<run_id>/stream")
@auth_required
def stream_run(run_id: str):
    runner = _runner()
    run = runner.get_run(run_id)
    if run is None:
        return jsonify({"error": "run_not_found", "run_id": run_id}), 404

    rl = _run_log()
    stop_event = threading.Event()
    group_id = run.group_id
    item_id = run.item_id

    @stream_with_context
    def generate():
        try:
            yield _sse_event("ready", "")
            for item in rl.stream(
                group_id,
                item_id,
                run_id,
                from_start=True,
                stop_event=stop_event,
                is_running=lambda: runner.is_running(run_id),
            ):
                if item is None:
                    yield ": ping\n\n"
                else:
                    yield _sse_event("line", item)
            yield _sse_event("end", "")
        except GeneratorExit:
            stop_event.set()
            raise

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return Response(generate(), mimetype="text/event-stream", headers=headers)


# ----------------------------------------------------------------------
# 외부 로그 파일 보기
# ----------------------------------------------------------------------


def _resolve_external_log(group_id: str, item_id: str, path: str) -> Path:
    """external_logs에 등록된 path만 허용한다.

    임의 경로 접근을 막기 위해 ActionItem.external_logs 안의 path와 정확히 일치할 때만
    통과시킨다. 추가로 allowed_path_roots 안인지도 확인.
    """
    registry = _registry()
    item = registry.get_action_item(group_id, item_id)
    matches = [ex for ex in item.external_logs if ex.path == path]
    if not matches:
        raise PermissionError("등록된 external_log이 아닙니다.")
    fb = _file_browser()
    if not fb.is_allowed(path):
        raise PermissionError("허용 루트 밖의 경로입니다.")
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    return target


@bp.get("/external_log")
@auth_required
def read_external_log():
    group_id = request.args.get("group_id", "")
    item_id = request.args.get("item_id", "")
    path = request.args.get("path", "")
    if not group_id or not item_id or not path:
        return jsonify({"error": "missing_params"}), 400

    try:
        registry = _registry()
        registry.get_action_item(group_id, item_id)
        target = _resolve_external_log(group_id, item_id, path)
    except (ActionGroupNotFoundError, ActionItemNotFoundError):
        return jsonify({"error": "action_item_not_found"}), 404
    except PermissionError as exc:
        return jsonify({"error": "path_not_allowed", "message": str(exc)}), 403
    except FileNotFoundError as exc:
        return jsonify({"error": "not_found", "message": str(exc)}), 404

    tail_n = request.args.get("tail", default=200, type=int) or 200
    lines, offset = _tail_file(target, tail_n)
    return jsonify({"lines": lines, "offset": offset})


@bp.get("/external_log/stream")
@auth_required
def stream_external_log():
    group_id = request.args.get("group_id", "")
    item_id = request.args.get("item_id", "")
    path = request.args.get("path", "")
    if not group_id or not item_id or not path:
        return jsonify({"error": "missing_params"}), 400

    try:
        registry = _registry()
        registry.get_action_item(group_id, item_id)
        target = _resolve_external_log(group_id, item_id, path)
    except (ActionGroupNotFoundError, ActionItemNotFoundError):
        return jsonify({"error": "action_item_not_found"}), 404
    except PermissionError as exc:
        return jsonify({"error": "path_not_allowed", "message": str(exc)}), 403
    except FileNotFoundError as exc:
        return jsonify({"error": "not_found", "message": str(exc)}), 404

    stop_event = threading.Event()

    @stream_with_context
    def generate():
        try:
            tail_lines, offset = _tail_file(target, 100)
            for line in tail_lines:
                yield _sse_event("line", line)
            yield _sse_event("ready", "")
            last_data_at = time.time()
            while True:
                if stop_event.is_set():
                    return
                if not target.is_file():
                    return
                size = target.stat().st_size
                if size > offset:
                    with target.open("rb") as fh:
                        fh.seek(offset)
                        data = fh.read()
                    text = data.decode("utf-8", errors="replace")
                    offset = size
                    last_data_at = time.time()
                    for line in text.splitlines():
                        yield _sse_event("line", line)
                else:
                    if time.time() - last_data_at > 15.0:
                        last_data_at = time.time()
                        yield ": ping\n\n"
                    time.sleep(0.5)
        except GeneratorExit:
            stop_event.set()
            raise

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return Response(generate(), mimetype="text/event-stream", headers=headers)


# ----------------------------------------------------------------------
# 유틸
# ----------------------------------------------------------------------


def _tail_file(path: Path, lines: int) -> tuple[list[str], int]:
    if not path.is_file() or lines <= 0:
        return [], 0
    size = path.stat().st_size
    block = 4096
    buf = b""
    with path.open("rb") as fh:
        pos = size
        while pos > 0 and buf.count(b"\n") <= lines:
            read = min(block, pos)
            pos -= read
            fh.seek(pos)
            buf = fh.read(read) + buf
    text = buf.decode("utf-8", errors="replace")
    return text.splitlines()[-lines:], size


def _sse_event(event: str, data: str) -> str:
    payload = json.dumps({"data": data}, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


# unused import 정리용 (linter 안내)
_ = os


__all__ = ["bp"]
