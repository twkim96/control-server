"""인증과 접근 제어.

정책:
* 세션 쿠키 + CSRF 토큰 방식.
* 쿠키 속성: HttpOnly, SameSite=Lax, Path=/.
* 로그인 엔드포인트는 응답 본문에 csrf_token을 포함한다.
* 프론트는 csrf_token을 메모리에 보관하고 mutation 호출에 X-CSRF-Token 헤더로 전송한다.
* SSE(GET) 엔드포인트는 쿠키만으로 인증한다 (EventSource는 임의 헤더를 못 붙이므로).

환경변수:
* CONTROL_PASSWORD: 필수. 비어있으면 로그인 불가 + dev 모드 안내 출력.
* CONTROL_SECRET_KEY: 선택. 미설정 시 backend/runtime/.secret_key 파일에 자동 생성.

저장:
* 세션 데이터는 Flask의 itsdangerous 기반 secure cookie에 직접 들어간다.
* CSRF 토큰은 세션과 같은 서버 측 secret으로 서명되며 클라이언트가 헤더로 보내준 값과
  hmac 비교한다.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from functools import wraps
from pathlib import Path
from typing import Callable

from flask import Flask, current_app, jsonify, request, session


PASSWORD_ENV = "CONTROL_PASSWORD"
SECRET_KEY_ENV = "CONTROL_SECRET_KEY"
CSRF_HEADER = "X-CSRF-Token"
SESSION_USER_KEY = "_user"
SESSION_CSRF_SEED_KEY = "_csrf_seed"

_log = logging.getLogger("server_control.auth")


# ----------------------------------------------------------------------
# 초기 설정
# ----------------------------------------------------------------------


def init_app(app: Flask, *, secret_key_path: str | os.PathLike[str]) -> None:
    """Flask 앱에 세션 관련 설정과 secret key를 적용한다."""

    secret = os.environ.get(SECRET_KEY_ENV)
    if secret:
        app.secret_key = secret.encode("utf-8")
    else:
        app.secret_key = _ensure_secret_key(Path(secret_key_path))

    # 외부 포트는 열지 않는 운영 전제. dev에서는 https가 아니므로 secure는 끔.
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("SESSION_COOKIE_SECURE", False)
    app.config.setdefault("SESSION_COOKIE_NAME", "server_control_session")
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", 60 * 60 * 24 * 90)


def _ensure_secret_key(path: Path) -> bytes:
    if path.is_file():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(48)
    path.write_bytes(secret)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _log.info("새 secret key를 생성했습니다: %s", path)
    return secret


# ----------------------------------------------------------------------
# 비밀번호 처리
# ----------------------------------------------------------------------


def expected_password() -> str | None:
    return os.environ.get(PASSWORD_ENV)


def is_password_configured() -> bool:
    pw = expected_password()
    return bool(pw and pw.strip())


def verify_password(provided: str) -> bool:
    expected = expected_password()
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


# ----------------------------------------------------------------------
# 세션 / CSRF
# ----------------------------------------------------------------------


def login_session(user: str = "control_user") -> str:
    """현재 요청을 인증된 상태로 만들고 csrf 토큰을 반환한다."""
    session.clear()
    session.permanent = True
    session[SESSION_USER_KEY] = user
    session[SESSION_CSRF_SEED_KEY] = secrets.token_hex(32)
    return _csrf_token_from_session()


def logout_session() -> None:
    session.clear()


def current_user() -> str | None:
    user = session.get(SESSION_USER_KEY)
    return user if isinstance(user, str) else None


def csrf_token() -> str:
    """현재 세션의 csrf 토큰. 세션이 없으면 빈 문자열."""
    if SESSION_CSRF_SEED_KEY not in session:
        return ""
    return _csrf_token_from_session()


def _csrf_token_from_session() -> str:
    seed = session.get(SESSION_CSRF_SEED_KEY, "")
    secret = current_app.secret_key or b""
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, str(seed).encode("utf-8"), hashlib.sha256).hexdigest()


def verify_csrf(provided: str) -> bool:
    expected = _csrf_token_from_session()
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)


# ----------------------------------------------------------------------
# 데코레이터
# ----------------------------------------------------------------------


def auth_required(fn: Callable) -> Callable:
    """로그인된 세션 쿠키가 있어야 통과."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_password_configured():
            return jsonify(
                {
                    "error": "password_not_configured",
                    "message": (
                        f"{PASSWORD_ENV} 환경변수가 설정되지 않았습니다. "
                        "컨트롤 서버는 모든 요청을 거부합니다."
                    ),
                }
            ), 503
        if current_user() is None:
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper


def csrf_required(fn: Callable) -> Callable:
    """mutation 호출에 적용. X-CSRF-Token 헤더 검증."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        provided = request.headers.get(CSRF_HEADER, "")
        if not verify_csrf(provided):
            return jsonify({"error": "csrf_failed"}), 403
        return fn(*args, **kwargs)

    return wrapper


__all__ = [
    "PASSWORD_ENV",
    "SECRET_KEY_ENV",
    "CSRF_HEADER",
    "init_app",
    "is_password_configured",
    "verify_password",
    "login_session",
    "logout_session",
    "current_user",
    "csrf_token",
    "verify_csrf",
    "auth_required",
    "csrf_required",
]
