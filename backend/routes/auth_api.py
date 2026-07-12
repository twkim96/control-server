"""인증 API.

* POST /api/auth/login   - 비밀번호로 로그인. 성공 시 세션 쿠키 + csrf_token 반환.
* POST /api/auth/logout  - 세션 무효화.
* GET  /api/auth/me      - 현재 세션 상태 + csrf_token 갱신 반환.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from auth import (
    csrf_token,
    current_user,
    is_password_configured,
    login_session,
    logout_session,
    verify_password,
)

bp = Blueprint("auth_api", __name__, url_prefix="/api/auth")
_log = logging.getLogger("server_control.auth_api")


@bp.post("/login")
def login():
    if not is_password_configured():
        return jsonify(
            {
                "error": "password_not_configured",
                "message": "CONTROL_PASSWORD 환경변수가 설정되지 않았습니다.",
            }
        ), 503

    payload = request.get_json(silent=True) or {}
    password = payload.get("password")
    if not isinstance(password, str) or not password:
        return jsonify({"error": "invalid_body"}), 400

    if not verify_password(password):
        _log.warning("login failed from %s", request.remote_addr)
        return jsonify({"error": "invalid_credentials"}), 401

    token = login_session()
    _log.info("login ok from %s", request.remote_addr)
    return jsonify({"ok": True, "user": current_user(), "csrf_token": token})


@bp.post("/logout")
def logout():
    logout_session()
    return jsonify({"ok": True})


@bp.get("/me")
def me():
    user = current_user()
    if user is None:
        return jsonify({"authenticated": False}), 200
    return jsonify(
        {
            "authenticated": True,
            "user": user,
            "csrf_token": csrf_token(),
        }
    )


__all__ = ["bp"]
