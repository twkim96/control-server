"""config.yml 안전한 쓰기.

안전 규칙:
* 저장 전 전체 설정을 재검증한다 (load_config로 round-trip).
* 기존 config.yml을 .bak로 백업한다.
* 임시 파일에 새 YAML을 쓴 뒤 atomic replace(`os.replace`)로 교체한다.
* 저장 실패(검증 실패 포함) 시 기존 파일은 유지한다.

서비스 등록/수정/삭제는 raw dict 단위로 처리한다. UI 입력값은
호출자가 미리 dict로 정리해서 넘긴다.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from threading import RLock
from pathlib import Path
from typing import Any, Callable

from ruamel.yaml import YAML

from config_loader import AppConfig, ConfigError, load_config


class ConfigWriteError(RuntimeError):
    pass


_path_locks_guard = RLock()
_path_locks: dict[Path, RLock] = {}


def _canonical_path(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve()


def _lock_for(path: str | os.PathLike[str]) -> RLock:
    canonical = _canonical_path(path)
    with _path_locks_guard:
        return _path_locks.setdefault(canonical, RLock())


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.indent(mapping=2, sequence=4, offset=2)
    y.preserve_quotes = True
    y.width = 120
    return y


def _read_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigWriteError(f"config 파일을 찾을 수 없습니다: {path}")
    yaml = _yaml()
    data = yaml.load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigWriteError("config 최상위는 mapping이어야 합니다.")
    return data


def save_config(path: str | os.PathLike[str], raw: dict[str, Any]) -> AppConfig:
    """주어진 raw dict를 검증한 뒤 atomic replace로 저장한다.

    검증에 실패하면 ConfigError를 raise하고 파일은 손대지 않는다.
    """

    config_path = _canonical_path(path)
    with _lock_for(config_path):
        return _save_config_locked(config_path, raw)


def _save_config_locked(config_path: Path, raw: dict[str, Any]) -> AppConfig:
    """caller가 config_path별 lock을 보유한 상태에서 검증·백업·교체한다."""
    yaml = _yaml()
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{config_path.stem}.", suffix=".tmp", dir=config_path.parent
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.dump(raw, fh)
        validated = load_config(tmp_path)

        if config_path.exists():
            backup = config_path.with_suffix(config_path.suffix + ".bak")
            shutil.copy2(config_path, backup)
        os.replace(tmp_path, config_path)
        return validated
    finally:
        tmp_path.unlink(missing_ok=True)


def _mutate_config(
    path: str | os.PathLike[str], mutate: Callable[[dict[str, Any]], None]
) -> AppConfig:
    config_path = _canonical_path(path)
    with _lock_for(config_path):
        raw = _read_raw(config_path)
        mutate(raw)
        return _save_config_locked(config_path, raw)


def upsert_service(
    path: str | os.PathLike[str],
    service: dict[str, Any],
    *,
    create_if_missing: bool = True,
) -> AppConfig:
    """단일 서비스 entry를 추가/갱신한 뒤 저장한다."""

    sid = service.get("id")
    if not isinstance(sid, str) or not sid:
        raise ConfigWriteError("service.id가 비어있습니다.")

    def mutate(raw: dict[str, Any]) -> None:
        services = raw.get("services") or []
        if not isinstance(services, list):
            raise ConfigWriteError("services는 리스트여야 합니다.")
        for i, item in enumerate(services):
            if isinstance(item, dict) and item.get("id") == sid:
                services[i] = service
                raw["services"] = services
                return
        if not create_if_missing:
            raise ConfigWriteError(f"서비스 ID를 찾지 못했습니다: {sid!r}")
        services.append(service)
        raw["services"] = services

    return _mutate_config(path, mutate)


def delete_service(path: str | os.PathLike[str], service_id: str) -> AppConfig:
    def mutate(raw: dict[str, Any]) -> None:
        services = raw.get("services") or []
        if not isinstance(services, list):
            raise ConfigWriteError("services는 리스트여야 합니다.")
        new_services = [s for s in services if not (isinstance(s, dict) and s.get("id") == service_id)]
        if len(new_services) == len(services):
            raise ConfigWriteError(f"서비스 ID를 찾지 못했습니다: {service_id!r}")
        raw["services"] = new_services

    return _mutate_config(path, mutate)


# ----------------------------------------------------------------------
# ActionGroup CRUD (root level `actions` 섹션)
# ----------------------------------------------------------------------


def upsert_action_group(
    path: str | os.PathLike[str],
    group: dict[str, Any],
    *,
    create_if_missing: bool = True,
) -> AppConfig:
    """ActionGroup 한 개를 추가/갱신한 뒤 저장한다."""
    gid = group.get("id")
    if not isinstance(gid, str) or not gid:
        raise ConfigWriteError("action_group.id가 비어있습니다.")

    def mutate(raw: dict[str, Any]) -> None:
        groups = raw.get("actions") or []
        if not isinstance(groups, list):
            raise ConfigWriteError("actions는 리스트여야 합니다.")
        for i, item in enumerate(groups):
            if isinstance(item, dict) and item.get("id") == gid:
                groups[i] = group
                raw["actions"] = groups
                return
        if not create_if_missing:
            raise ConfigWriteError(f"action_group ID를 찾지 못했습니다: {gid!r}")
        groups.append(group)
        raw["actions"] = groups

    return _mutate_config(path, mutate)


def delete_action_group(path: str | os.PathLike[str], group_id: str) -> AppConfig:
    def mutate(raw: dict[str, Any]) -> None:
        groups = raw.get("actions") or []
        if not isinstance(groups, list):
            raise ConfigWriteError("actions는 리스트여야 합니다.")
        new_groups = [g for g in groups if not (isinstance(g, dict) and g.get("id") == group_id)]
        if len(new_groups) == len(groups):
            raise ConfigWriteError(f"action_group ID를 찾지 못했습니다: {group_id!r}")
        raw["actions"] = new_groups

    return _mutate_config(path, mutate)


# ----------------------------------------------------------------------
# 순서 변경 (Services / ActionGroups)
# ----------------------------------------------------------------------


def _reorder_list(
    items: list[Any],
    order: list[str],
    *,
    label: str,
) -> list[Any]:
    """주어진 dict 리스트를 order(id 리스트)대로 재정렬해 반환한다.

    - order에 누락된 id 또는 알 수 없는 id가 있으면 ConfigWriteError.
    - id가 없는 entry나 dict가 아닌 entry는 그대로 거부.
    """
    by_id: dict[str, Any] = {}
    for entry in items:
        if not isinstance(entry, dict):
            raise ConfigWriteError(f"{label} 리스트에 mapping이 아닌 entry가 있습니다.")
        eid = entry.get("id")
        if not isinstance(eid, str):
            raise ConfigWriteError(f"{label} entry에 id가 없습니다.")
        by_id[eid] = entry

    if len(order) != len(set(order)):
        raise ConfigWriteError(f"{label} 순서에 중복된 id가 있습니다.")

    if set(order) != set(by_id.keys()):
        missing = sorted(set(by_id.keys()) - set(order))
        unknown = sorted(set(order) - set(by_id.keys()))
        raise ConfigWriteError(
            f"{label} 순서가 현재 등록 목록과 일치하지 않습니다. "
            f"missing={missing}, unknown={unknown}"
        )

    return [by_id[eid] for eid in order]


def reorder_services(path: str | os.PathLike[str], order: list[str]) -> AppConfig:
    def mutate(raw: dict[str, Any]) -> None:
        services = raw.get("services") or []
        if not isinstance(services, list):
            raise ConfigWriteError("services는 리스트여야 합니다.")
        raw["services"] = _reorder_list(services, order, label="services")

    return _mutate_config(path, mutate)


def reorder_action_groups(
    path: str | os.PathLike[str], order: list[str]
) -> AppConfig:
    def mutate(raw: dict[str, Any]) -> None:
        groups = raw.get("actions") or []
        if not isinstance(groups, list):
            raise ConfigWriteError("actions는 리스트여야 합니다.")
        raw["actions"] = _reorder_list(groups, order, label="actions")

    return _mutate_config(path, mutate)


__all__ = [
    "ConfigWriteError",
    "save_config",
    "upsert_service",
    "delete_service",
    "upsert_action_group",
    "delete_action_group",
    "reorder_services",
    "reorder_action_groups",
]
