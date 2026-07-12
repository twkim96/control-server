"""Server-side appearance settings storage."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, TypedDict


class AppearanceSettings(TypedDict):
    backgroundColor: str
    textColor: str
    accentColor: str


DEFAULT_APPEARANCE_SETTINGS: AppearanceSettings = {
    "backgroundColor": "#0b0d10",
    "textColor": "#e7ebf0",
    "accentColor": "#3b82f6",
}

HEX_COLOR_RE = re.compile(r"^#[0-9a-f]{6}$", re.IGNORECASE)


def read_appearance(path: str | os.PathLike[str]) -> tuple[AppearanceSettings, bool]:
    """Return settings and whether a server file exists."""

    store_path = Path(path)
    if not store_path.is_file():
        return DEFAULT_APPEARANCE_SETTINGS.copy(), False
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_APPEARANCE_SETTINGS.copy(), False
    return normalize_appearance(raw), True


def write_appearance(
    path: str | os.PathLike[str],
    payload: Any,
) -> AppearanceSettings:
    settings = normalize_appearance(payload)
    store_path = Path(path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = store_path.with_suffix(store_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(store_path)
    try:
        os.chmod(store_path, 0o600)
    except OSError:
        pass
    return settings


def reset_appearance(path: str | os.PathLike[str]) -> AppearanceSettings:
    store_path = Path(path)
    try:
        store_path.unlink()
    except FileNotFoundError:
        pass
    return DEFAULT_APPEARANCE_SETTINGS.copy()


def normalize_appearance(payload: Any) -> AppearanceSettings:
    if not isinstance(payload, dict):
        return DEFAULT_APPEARANCE_SETTINGS.copy()
    return {
        "backgroundColor": _normalize_hex(
            payload.get("backgroundColor"),
            DEFAULT_APPEARANCE_SETTINGS["backgroundColor"],
        ),
        "textColor": _normalize_hex(
            payload.get("textColor"),
            DEFAULT_APPEARANCE_SETTINGS["textColor"],
        ),
        "accentColor": _normalize_hex(
            payload.get("accentColor"),
            DEFAULT_APPEARANCE_SETTINGS["accentColor"],
        ),
    }


def _normalize_hex(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    trimmed = value.strip()
    if HEX_COLOR_RE.match(trimmed):
        return trimmed.lower()
    return fallback
