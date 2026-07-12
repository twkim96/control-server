"""config 등록/수정/삭제와 파일 탐색 API.

엔드포인트:
* GET    /api/config/services
* POST   /api/config/services
* PUT    /api/config/services/<sid>
* DELETE /api/config/services/<sid>
* POST   /api/config/reload
* GET    /api/files

경로 입력은 모두 file_browser로 검증한다.
"""

from __future__ import annotations

import logging
from threading import RLock
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from auth import auth_required, csrf_required
from config_loader import ConfigError, load_config, validate_command_safety
from config_writer import (
    ConfigWriteError,
    delete_action_group,
    delete_service,
    reorder_action_groups,
    reorder_services,
    upsert_action_group,
    upsert_service,
)
from file_browser import FileBrowser, PathNotAllowedError, entry_to_dict
from service_registry import ServiceRegistry
from process_manager import ProcessError, ProcessManager

bp = Blueprint("config_api", __name__, url_prefix="/api")
_log = logging.getLogger("server_control.config_api")
_config_reconcile_lock = RLock()


def _registry() -> ServiceRegistry:
    return current_app.config["registry"]


def _config_path() -> Path:
    return Path(current_app.config["config_path"])


def _file_browser() -> FileBrowser:
    return current_app.config["file_browser"]


def _process_manager() -> ProcessManager:
    return current_app.config["process_manager"]


def _reload_registry() -> None:
    with _config_reconcile_lock:
        new_cfg = load_config(_config_path())
        _process_manager().reconcile_service_definitions(new_cfg.services)
        _registry().reload(new_cfg)
        # 파일 브라우저는 controller.allowed_path_roots를 따른다.
        current_app.config["file_browser"] = FileBrowser(new_cfg.controller.allowed_path_roots)


def _validate_cwd(payload: dict[str, Any]):
    """payload의 cwd가 allowed_path_roots 안에 있는지 backend에서 재검증.

    UI PathPicker가 보장하더라도 API 직접 호출 케이스를 막기 위함.
    cwd가 비어있으면 config_loader에서 별도로 거부한다.
    """
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None  # config_loader가 거부하도록 통과
    if not _file_browser().is_allowed(cwd):
        return jsonify(
            {
                "error": "cwd_not_allowed",
                "message": f"working directory가 허용 루트 밖에 있습니다: {cwd}",
            }
        ), 403
    return None


# ----------------------------------------------------------------------
# 서비스 CRUD
# ----------------------------------------------------------------------


@bp.get("/config/services")
@auth_required
def list_service_configs():
    services = _registry().list_services()
    return jsonify(
        {"services": [_registry().service_to_meta(s) for s in services]}
    )


@bp.post("/config/services")
@auth_required
@csrf_required
def create_service():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_body"}), 400
    cwd_error = _validate_cwd(payload)
    if cwd_error is not None:
        return cwd_error
    try:
        with _config_reconcile_lock:
            with _process_manager().service_operation(payload["id"]):
                upsert_service(_config_path(), payload, create_if_missing=True)
                _reload_registry()
    except ConfigError as exc:
        return jsonify({"error": "config_invalid", "message": str(exc)}), 400
    except ConfigWriteError as exc:
        return jsonify({"error": "config_write_failed", "message": str(exc)}), 400
    except ProcessError as exc:
        return jsonify({"error": "config_reload_blocked", "message": str(exc)}), 409

    return jsonify({"ok": True, "service": _registry().service_to_meta(_registry().get(payload["id"]))})


@bp.put("/config/services/<sid>")
@auth_required
@csrf_required
def update_service(sid: str):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_body"}), 400
    if payload.get("id") not in (None, sid):
        return jsonify({"error": "id_mismatch", "url_id": sid, "body_id": payload.get("id")}), 400
    payload["id"] = sid
    cwd_error = _validate_cwd(payload)
    if cwd_error is not None:
        return cwd_error
    try:
        with _config_reconcile_lock:
            with _process_manager().service_operation(sid):
                upsert_service(_config_path(), payload, create_if_missing=False)
                _reload_registry()
    except ConfigError as exc:
        return jsonify({"error": "config_invalid", "message": str(exc)}), 400
    except ConfigWriteError as exc:
        return jsonify({"error": "config_write_failed", "message": str(exc)}), 400
    except ProcessError as exc:
        return jsonify({"error": "config_reload_blocked", "message": str(exc)}), 409

    return jsonify({"ok": True, "service": _registry().service_to_meta(_registry().get(sid))})


@bp.delete("/config/services/<sid>")
@auth_required
@csrf_required
def remove_service(sid: str):
    try:
        with _config_reconcile_lock:
            pm = _process_manager()
            with pm.service_operation(sid):
                state, alive = pm.inspect_state(sid)
                if alive:
                    return jsonify(
                        {
                            "error": "service_running",
                            "message": f"{sid}는 실행 중입니다. 먼저 중지하세요.",
                            "pid": state.pid,
                        }
                    ), 409
                delete_service(_config_path(), sid)
                _reload_registry()
                pm.forget_service(sid)
    except ConfigWriteError as exc:
        return jsonify({"error": "config_write_failed", "message": str(exc)}), 400
    except ConfigError as exc:
        return jsonify({"error": "config_invalid", "message": str(exc)}), 400
    except ProcessError as exc:
        return jsonify({"error": "service_running", "message": str(exc)}), 409
    return jsonify({"ok": True})


@bp.post("/config/services/reorder")
@auth_required
@csrf_required
def reorder_services_route():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_body"}), 400
    order = payload.get("order")
    if not isinstance(order, list) or not all(isinstance(s, str) for s in order):
        return jsonify({"error": "invalid_order"}), 400
    try:
        reorder_services(_config_path(), order)
        _reload_registry()
    except ConfigWriteError as exc:
        return jsonify({"error": "reorder_failed", "message": str(exc)}), 400
    except ConfigError as exc:
        return jsonify({"error": "config_invalid", "message": str(exc)}), 400
    return jsonify({"ok": True})


@bp.post("/config/reload")
@auth_required
@csrf_required
def reload_config():
    try:
        _reload_registry()
    except ConfigError as exc:
        return jsonify({"error": "config_invalid", "message": str(exc)}), 400
    except ProcessError as exc:
        return jsonify({"error": "config_reload_blocked", "message": str(exc)}), 409
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# ActionGroup CRUD (root level `actions` 섹션)
# ----------------------------------------------------------------------


def _validate_action_payload(payload: dict[str, Any]):
    """ActionGroup payload를 등록 시점에 검증한다.

    config_writer가 save_config 내부에서 다시 한번 검증하지만, 친절한 에러 메시지를
    위해 여기서도 한 번 검증한다. 또한 python kind의 cwd allowed_path_roots 검증과
    deny list 검증을 backend에서 한 번 더 수행해 UI 우회를 막는다.
    """
    fb = _file_browser()
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"error": "items_required"}), 400

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            return jsonify({"error": "invalid_item", "index": idx}), 400
        kind = item.get("kind")
        cwd = item.get("cwd")
        command = item.get("command")
        if kind == "python":
            if not isinstance(cwd, str) or not cwd:
                return jsonify({"error": "cwd_required", "index": idx}), 400
            if not fb.is_allowed(cwd):
                return (
                    jsonify(
                        {
                            "error": "cwd_not_allowed",
                            "index": idx,
                            "message": f"working directory가 허용 루트 밖에 있습니다: {cwd}",
                        }
                    ),
                    403,
                )
        if not isinstance(command, list) or not command:
            return jsonify({"error": "command_required", "index": idx}), 400
        try:
            validate_command_safety(
                tuple(command),
                kind=str(kind or ""),
                where=f"items[{idx}].command",
            )
        except ConfigError as exc:
            return jsonify({"error": "command_blocked", "message": str(exc)}), 400
    return None


@bp.get("/config/actions")
@auth_required
def list_action_groups():
    registry = _registry()
    groups = [registry.action_group_to_meta(g) for g in registry.list_action_groups()]
    return jsonify({"groups": groups})


@bp.post("/config/actions")
@auth_required
@csrf_required
def create_action_group():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_body"}), 400
    err = _validate_action_payload(payload)
    if err is not None:
        return err
    try:
        upsert_action_group(_config_path(), payload, create_if_missing=True)
        _reload_registry()
    except ConfigError as exc:
        return jsonify({"error": "config_invalid", "message": str(exc)}), 400
    except ConfigWriteError as exc:
        return jsonify({"error": "config_write_failed", "message": str(exc)}), 400
    return jsonify(
        {
            "ok": True,
            "group": _registry().action_group_to_meta(
                _registry().get_action_group(payload["id"])
            ),
        }
    )


@bp.put("/config/actions/<gid>")
@auth_required
@csrf_required
def update_action_group(gid: str):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_body"}), 400
    if payload.get("id") not in (None, gid):
        return jsonify(
            {"error": "id_mismatch", "url_id": gid, "body_id": payload.get("id")}
        ), 400
    payload["id"] = gid
    err = _validate_action_payload(payload)
    if err is not None:
        return err
    try:
        upsert_action_group(_config_path(), payload, create_if_missing=False)
        _reload_registry()
    except ConfigError as exc:
        return jsonify({"error": "config_invalid", "message": str(exc)}), 400
    except ConfigWriteError as exc:
        return jsonify({"error": "config_write_failed", "message": str(exc)}), 400
    return jsonify(
        {
            "ok": True,
            "group": _registry().action_group_to_meta(_registry().get_action_group(gid)),
        }
    )


@bp.delete("/config/actions/<gid>")
@auth_required
@csrf_required
def remove_action_group(gid: str):
    try:
        delete_action_group(_config_path(), gid)
        _reload_registry()
    except ConfigWriteError as exc:
        return jsonify({"error": "config_write_failed", "message": str(exc)}), 400
    except ConfigError as exc:
        return jsonify({"error": "config_invalid", "message": str(exc)}), 400
    return jsonify({"ok": True})


@bp.post("/config/actions/reorder")
@auth_required
@csrf_required
def reorder_action_groups_route():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_body"}), 400
    order = payload.get("order")
    if not isinstance(order, list) or not all(isinstance(s, str) for s in order):
        return jsonify({"error": "invalid_order"}), 400
    try:
        reorder_action_groups(_config_path(), order)
        _reload_registry()
    except ConfigWriteError as exc:
        return jsonify({"error": "reorder_failed", "message": str(exc)}), 400
    except ConfigError as exc:
        return jsonify({"error": "config_invalid", "message": str(exc)}), 400
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# 파일 탐색
# ----------------------------------------------------------------------


@bp.get("/files")
@auth_required
def list_files():
    target = request.args.get("path")
    fb = _file_browser()
    if not target:
        return jsonify(
            {
                "roots": [entry_to_dict(e) for e in fb.list_roots()],
                "entries": [],
            }
        )
    try:
        entries = fb.list_dir(target)
    except PathNotAllowedError as exc:
        return jsonify({"error": "path_not_allowed", "message": str(exc)}), 403
    except FileNotFoundError as exc:
        return jsonify({"error": "not_found", "message": str(exc)}), 404
    except NotADirectoryError as exc:
        return jsonify({"error": "not_a_directory", "message": str(exc)}), 400

    return jsonify(
        {
            "path": target,
            "entries": [entry_to_dict(e) for e in entries],
        }
    )


__all__ = ["bp"]
