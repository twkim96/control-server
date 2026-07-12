"""config.yml의 스키마 정의 (frozen dataclasses).

검증/파싱 로직(config_loader.py)과 분리한 순수 데이터 모델 모음. 다른 모듈은 보통
`from config_loader import ServiceConfig ...`로 가져오지만, config_loader가 이 모듈을
re-export하므로 어느 쪽에서 import해도 동일하다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ControllerConfig:
    host: str
    port: int
    editable_config: bool
    allowed_path_roots: tuple[str, ...]
    auth_type: str
    auth_password_env: str | None


@dataclass(frozen=True)
class HealthConfig:
    enabled: bool
    type: str
    url: str | None
    timeout_seconds: float
    verify_ssl: bool


@dataclass(frozen=True)
class HttpsConfig:
    enabled: bool
    cert_file: str | None
    key_file: str | None
    enabled_env_name: str
    cert_file_env_name: str
    key_file_env_name: str


def _default_https_config() -> HttpsConfig:
    return HttpsConfig(
        enabled=False,
        cert_file=None,
        key_file=None,
        enabled_env_name="HTTPS",
        cert_file_env_name="SSL_CERT_FILE",
        key_file_env_name="SSL_KEY_FILE",
    )


@dataclass(frozen=True)
class LogConfig:
    enabled: bool
    tail_lines: int
    max_bytes: int  # 단일 로그 파일의 최대 크기 (회전 임계). 0이면 회전 안 함
    keep: int  # 회전 후 보관할 과거 파일 수. 0 또는 1 이상


@dataclass(frozen=True)
class LifecycleConfig:
    mode: str
    autostart: bool
    stop_visibility: str
    restart_visibility: str
    unmanaged_policy: str


@dataclass(frozen=True)
class StopStrategy:
    signal: str
    timeout_seconds: float
    confirm_required: bool
    fallback: tuple[str, ...]


@dataclass(frozen=True)
class ActionConfig:
    id: str
    label: str
    type: str
    enabled: bool
    stop_strategy: StopStrategy | None = None


@dataclass(frozen=True)
class ServiceConfig:
    id: str
    name: str
    description: str
    cwd: str
    entry_file: str
    command: tuple[str, ...]
    env: dict[str, str]
    port: int | None
    port_env_name: str | None  # 채워져 있으면 자식 env에 자동 주입됨
    open_url: str | None
    health: HealthConfig
    log: LogConfig
    lifecycle: LifecycleConfig
    actions: tuple[ActionConfig, ...]
    https: HttpsConfig = field(default_factory=_default_https_config)
    adopt_command: tuple[str, ...] | None = None
    adopt_match: str = "exact"


@dataclass(frozen=True)
class ExternalLogConfig:
    """ActionItem이 직접 만드는 로그 파일을 화면에서 함께 보기 위한 설정."""

    name: str
    path: str


@dataclass(frozen=True)
class ActionRunLogConfig:
    enabled: bool
    keep_runs: int  # ActionItem당 디스크에 보관할 .log 파일 개수


@dataclass(frozen=True)
class ActionItemConfig:
    """일회성 명령(액션) 한 개."""

    id: str
    name: str
    description: str
    kind: str  # "python" | "argv"
    cwd: str | None
    command: tuple[str, ...]
    env: dict[str, str]
    log: ActionRunLogConfig
    external_logs: tuple[ExternalLogConfig, ...]


@dataclass(frozen=True)
class ActionGroupConfig:
    """ActionItem 여러 개를 묶은 카드 단위 그룹."""

    id: str
    name: str
    description: str
    items: tuple[ActionItemConfig, ...]


@dataclass(frozen=True)
class AppConfig:
    controller: ControllerConfig
    services: tuple[ServiceConfig, ...]
    action_groups: tuple[ActionGroupConfig, ...] = ()
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


__all__ = [
    "ControllerConfig",
    "HealthConfig",
    "HttpsConfig",
    "LogConfig",
    "LifecycleConfig",
    "StopStrategy",
    "ActionConfig",
    "ServiceConfig",
    "ExternalLogConfig",
    "ActionRunLogConfig",
    "ActionItemConfig",
    "ActionGroupConfig",
    "AppConfig",
]
