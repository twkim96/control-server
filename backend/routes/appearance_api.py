"""Appearance settings API."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from appearance_store import (
    read_appearance,
    reset_appearance,
    write_appearance,
)
from auth import auth_required, csrf_required


bp = Blueprint("appearance_api", __name__, url_prefix="/api/settings")


def _appearance_path() -> Path:
    return Path(current_app.config["appearance_store_path"])


@bp.get("/appearance")
def get_appearance():
    settings, persisted = read_appearance(_appearance_path())
    return jsonify({"settings": settings, "persisted": persisted})


@bp.put("/appearance")
@auth_required
@csrf_required
def update_appearance():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_body"}), 400
    settings_payload = payload.get("settings", payload)
    settings = write_appearance(_appearance_path(), settings_payload)
    return jsonify({"ok": True, "settings": settings, "persisted": True})


@bp.delete("/appearance")
@auth_required
@csrf_required
def delete_appearance():
    settings = reset_appearance(_appearance_path())
    return jsonify({"ok": True, "settings": settings, "persisted": False})
