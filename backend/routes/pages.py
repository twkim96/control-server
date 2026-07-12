"""SPA 라우트 fallback.

빌드된 React 앱(`frontend/dist/index.html`)을 모든 비-API 경로에 응답한다.
프론트가 아직 빌드되지 않았으면 안내 페이지를 보낸다.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, send_file, send_from_directory

bp = Blueprint("pages", __name__)


def _frontend_dist() -> Path:
    return Path(current_app.config["frontend_dist"])


def _index_html() -> Path:
    return _frontend_dist() / "index.html"


def _placeholder_html() -> str:
    return (
        "<!doctype html>"
        "<html><head><meta charset='utf-8'><title>server_control</title></head>"
        "<body style='font-family:system-ui;background:#0b0d10;color:#d8dde6;padding:32px'>"
        "<h1>frontend/dist 가 비어있습니다</h1>"
        "<p>개발 모드에서는 <code>npm run dev</code>로 vite 서버를 별도로 띄우거나,"
        " 프로덕션 모드에서는 <code>npm run build</code>를 먼저 실행하세요.</p>"
        "<p>API는 <code>/api/services</code> 등으로 정상 동작합니다.</p>"
        "</body></html>"
    )


@bp.get("/")
def index():
    idx = _index_html()
    if idx.is_file():
        return send_file(idx)
    return _placeholder_html()


@bp.get("/<path:path>")
def spa_fallback(path: str):
    """API가 아닌 모든 경로는 index.html로 fallback (SPA 라우팅)."""
    if path.startswith("api/"):
        return {"error": "not_found"}, 404

    dist = _frontend_dist()
    candidate = (dist / path).resolve()
    try:
        candidate.relative_to(dist.resolve())
    except (ValueError, FileNotFoundError):
        candidate = None

    if candidate is not None and candidate.is_file():
        return send_from_directory(dist, path)

    idx = _index_html()
    if idx.is_file():
        return send_file(idx)
    return _placeholder_html()


__all__ = ["bp"]
