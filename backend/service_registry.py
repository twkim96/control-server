"""등록된 서비스 목록을 메모리에 보관하고 조회/메타데이터 변환을 담당한다.

`config_loader.load_config`로 만든 `AppConfig`를 받아 보관하고,
서비스 ID로의 빠른 조회와 UI 응답용 메타데이터 변환을 제공한다.

config 파일이 외부에서 수정되었을 때는 `reload(new_config)`를 호출해 교체한다.
"""

from __future__ import annotations

from threading import RLock
from typing import Any

from config_loader import (
    ActionConfig,
    ActionGroupConfig,
    ActionItemConfig,
    AppConfig,
    ServiceConfig,
)


class ServiceNotFoundError(KeyError):
    """등록되지 않은 서비스 ID를 참조했을 때 발생."""


class ActionNotFoundError(KeyError):
    """서비스에 정의되지 않은 action ID를 참조했을 때 발생."""


class ActionGroupNotFoundError(KeyError):
    """등록되지 않은 ActionGroup id를 참조했을 때 발생."""


class ActionItemNotFoundError(KeyError):
    """ActionGroup 안에 정의되지 않은 ActionItem id를 참조했을 때 발생."""


class ServiceRegistry:
    def __init__(self, config: AppConfig) -> None:
        self._lock = RLock()
        self._config = config
        self._index: dict[str, ServiceConfig] = {s.id: s for s in config.services}
        self._action_index: dict[str, ActionGroupConfig] = {
            g.id: g for g in config.action_groups
        }

    @property
    def config(self) -> AppConfig:
        with self._lock:
            return self._config

    def reload(self, new_config: AppConfig) -> None:
        with self._lock:
            self._config = new_config
            self._index = {s.id: s for s in new_config.services}
            self._action_index = {g.id: g for g in new_config.action_groups}

    def list_services(self) -> tuple[ServiceConfig, ...]:
        with self._lock:
            return self._config.services

    def get(self, service_id: str) -> ServiceConfig:
        with self._lock:
            try:
                return self._index[service_id]
            except KeyError as exc:
                raise ServiceNotFoundError(service_id) from exc

    def get_action(self, service_id: str, action_id: str) -> ActionConfig:
        service = self.get(service_id)
        for action in service.actions:
            if action.id == action_id:
                return action
        raise ActionNotFoundError(f"{service_id}/{action_id}")

    # ------------------------------------------------------------------
    # ActionGroup / ActionItem
    # ------------------------------------------------------------------

    def list_action_groups(self) -> tuple[ActionGroupConfig, ...]:
        with self._lock:
            return self._config.action_groups

    def get_action_group(self, group_id: str) -> ActionGroupConfig:
        with self._lock:
            try:
                return self._action_index[group_id]
            except KeyError as exc:
                raise ActionGroupNotFoundError(group_id) from exc

    def get_action_item(self, group_id: str, item_id: str) -> ActionItemConfig:
        group = self.get_action_group(group_id)
        for item in group.items:
            if item.id == item_id:
                return item
        raise ActionItemNotFoundError(f"{group_id}/{item_id}")

    def action_group_to_meta(self, group: ActionGroupConfig) -> dict[str, Any]:
        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "items": [self._action_item_to_meta(item) for item in group.items],
        }

    def _action_item_to_meta(self, item: ActionItemConfig) -> dict[str, Any]:
        return {
            "id": item.id,
            "name": item.name,
            "description": item.description,
            "kind": item.kind,
            "cwd": item.cwd,
            "command": list(item.command),
            "env": dict(item.env),
            "log": {
                "enabled": item.log.enabled,
                "keep_runs": item.log.keep_runs,
            },
            "external_logs": [
                {"name": ex.name, "path": ex.path} for ex in item.external_logs
            ],
        }

    # ------------------------------------------------------------------
    # API 응답용 직렬화
    # ------------------------------------------------------------------

    def service_to_meta(self, service: ServiceConfig) -> dict[str, Any]:
        """대시보드 목록/상세에 사용할 정적 메타데이터 (런타임 상태는 별도 합성)."""
        return {
            "id": service.id,
            "name": service.name,
            "description": service.description,
            "cwd": service.cwd,
            "entry_file": service.entry_file,
            "command": list(service.command),
            "adopt_command": (
                list(service.adopt_command) if service.adopt_command is not None else None
            ),
            "adopt_match": service.adopt_match,
            "env": dict(service.env),
            "port": service.port,
            "port_env_name": service.port_env_name,
            "open_url": service.open_url,
            "https": {
                "enabled": service.https.enabled,
                "cert_file": service.https.cert_file,
                "key_file": service.https.key_file,
                "env": {
                    "enabled": service.https.enabled_env_name,
                    "cert_file": service.https.cert_file_env_name,
                    "key_file": service.https.key_file_env_name,
                },
            },
            "health": {
                "enabled": service.health.enabled,
                "type": service.health.type,
                "url": service.health.url,
                "timeout_seconds": service.health.timeout_seconds,
                "verify_ssl": service.health.verify_ssl,
            },
            "log": {
                "enabled": service.log.enabled,
                "tail_lines": service.log.tail_lines,
                "max_bytes": service.log.max_bytes,
                "keep": service.log.keep,
            },
            "lifecycle": {
                "mode": service.lifecycle.mode,
                "autostart": service.lifecycle.autostart,
                "stop_visibility": service.lifecycle.stop_visibility,
                "restart_visibility": service.lifecycle.restart_visibility,
                "unmanaged_policy": service.lifecycle.unmanaged_policy,
            },
            "actions": [self._action_to_meta(a) for a in service.actions],
        }

    def _action_to_meta(self, action: ActionConfig) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "id": action.id,
            "label": action.label,
            "type": action.type,
            "enabled": action.enabled,
        }
        if action.stop_strategy is not None:
            meta["strategy"] = {
                "signal": action.stop_strategy.signal,
                "timeout_seconds": action.stop_strategy.timeout_seconds,
                "confirm_required": action.stop_strategy.confirm_required,
                "fallback": list(action.stop_strategy.fallback),
            }
        return meta
