"""config.yml 로딩과 검증.

이 모듈은 컨트롤 서버 전체 설정을 읽고 스키마를 검증한다.
검증에 실패하면 ConfigError를 raise하며, 이는 컨트롤 서버 시작 전에
명확한 오류 메시지로 출력되어야 한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


# v1에서 허용하는 액션 타입.
# 외부 인스턴스 종료는 actions 배열을 거치지 않고 별도 엔드포인트
# (POST /api/services/<sid>/kill_external)로 호출되므로 여기 포함하지 않는다.
ALLOWED_ACTION_TYPES: set[str] = {
    "process_start",
    "process_stop",
    "process_restart",
    "health_check",
    "show_logs",
    "open_url",
    "edit_config",
}

ALLOWED_LIFECYCLE_MODES: set[str] = {"manual", "always_on"}
ALLOWED_VISIBILITY: set[str] = {"primary", "danger_menu", "hidden"}
ALLOWED_UNMANAGED_POLICY: set[str] = {"status_only", "manage"}

# Action(일회성 명령) 종류.
# python: cwd + python interpreter command, cwd는 allowed_path_roots 안에 있어야 함.
# argv : 시스템 명령. deny list로 위험 명령 차단.
ALLOWED_ACTION_KINDS: set[str] = {"python", "argv"}

# argv kind 등록 시 거부할 명령 basename 목록.
# 절대경로/상대경로/인터프리터 우회를 모두 막기 위해 basename 비교를 한다.
ACTION_DENY_BASENAMES: frozenset[str] = frozenset(
    {
        "sudo",
        "su",
        "rm",
        "mv",
        "dd",
        "chmod",
        "chown",
        "kill",
        "pkill",
        "killall",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
    }
)

# 단독 셸 실행 차단(첫 인자 없이 실행).
# 단독이 아니어도 -c/-e 조합이면 임의 코드 실행이라 별도 차단.
ACTION_SHELL_BASENAMES: frozenset[str] = frozenset(
    {"bash", "sh", "zsh", "fish", "ksh", "csh", "tcsh", "dash"}
)

# 인터프리터 + -c/-e 조합으로 임의 코드를 실행할 수 있는 바이너리.
ACTION_EVAL_BASENAMES: frozenset[str] = frozenset(
    {"python", "python2", "python3", "ruby", "perl", "node", "deno"}
)


class ConfigError(ValueError):
    """설정이 잘못되었을 때 발생하는 예외."""


# 스키마(dataclass)는 config_schema로 분리했다. 기존 `from config_loader import
# ServiceConfig ...` 호출부 호환을 위해 여기서 re-export한다.
from config_schema import (  # noqa: E402
    ActionConfig,
    ActionGroupConfig,
    ActionItemConfig,
    ActionRunLogConfig,
    AppConfig,
    ControllerConfig,
    ExternalLogConfig,
    HealthConfig,
    HttpsConfig,
    LifecycleConfig,
    LogConfig,
    ServiceConfig,
    StopStrategy,
    _default_https_config,
)


def load_config(path: str | os.PathLike[str]) -> AppConfig:
    """주어진 경로의 YAML을 읽어 AppConfig로 반환한다.

    파일이 없거나 스키마가 깨지면 ConfigError를 raise한다.
    """

    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config 파일을 찾을 수 없습니다: {config_path}")

    yaml = YAML(typ="rt")
    try:
        raw = yaml.load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # ruamel raises various subclasses
        raise ConfigError(f"config 파싱 실패: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config 최상위는 mapping 이어야 합니다.")

    controller = _parse_controller(raw.get("controller"))
    services = _parse_services(raw.get("services"))
    _validate_network_config(controller, services)
    action_groups = _parse_action_groups(raw.get("actions"))

    return AppConfig(
        controller=controller,
        services=services,
        action_groups=action_groups,
        raw=dict(raw),
    )


def _parse_controller(node: Any) -> ControllerConfig:
    if not isinstance(node, dict):
        raise ConfigError("`controller` 섹션이 누락되었거나 mapping이 아닙니다.")

    host = _require_str(node, "controller.host")
    port = _require_int(node, "controller.port")
    editable_config = bool(node.get("editable_config", True))

    roots = node.get("allowed_path_roots") or []
    if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
        raise ConfigError("`controller.allowed_path_roots`는 문자열 리스트여야 합니다.")

    auth_node = node.get("auth") or {}
    if not isinstance(auth_node, dict):
        raise ConfigError("`controller.auth`는 mapping이어야 합니다.")
    auth_type = str(auth_node.get("type", "password"))
    auth_password_env = auth_node.get("password_env")
    if auth_password_env is not None and not isinstance(auth_password_env, str):
        raise ConfigError("`controller.auth.password_env`는 문자열이어야 합니다.")

    return ControllerConfig(
        host=host,
        port=port,
        editable_config=editable_config,
        allowed_path_roots=tuple(roots),
        auth_type=auth_type,
        auth_password_env=auth_password_env,
    )


def _parse_services(node: Any) -> tuple[ServiceConfig, ...]:
    if node is None:
        return ()
    if not isinstance(node, list):
        raise ConfigError("`services`는 리스트여야 합니다.")

    seen_ids: set[str] = set()
    services: list[ServiceConfig] = []
    for index, item in enumerate(node):
        service = _parse_service(item, index)
        if service.id in seen_ids:
            raise ConfigError(f"서비스 ID 중복: {service.id!r}")
        seen_ids.add(service.id)
        services.append(service)

    return tuple(services)


def _parse_service(node: Any, index: int) -> ServiceConfig:
    where = f"services[{index}]"
    if not isinstance(node, dict):
        raise ConfigError(f"{where}는 mapping이어야 합니다.")

    sid = _require_str(node, f"{where}.id")
    if not _is_valid_id(sid):
        raise ConfigError(
            f"{where}.id는 영문/숫자/`_`/`-`로만 구성되어야 합니다: {sid!r}"
        )

    name = _require_str(node, f"{where}.name")
    description = str(node.get("description", ""))
    cwd = _require_str(node, f"{where}.cwd")
    entry_file = _require_str(node, f"{where}.entry_file")

    command_node = node.get("command")
    if (
        not isinstance(command_node, list)
        or not command_node
        or not all(isinstance(c, str) for c in command_node)
    ):
        raise ConfigError(f"{where}.command는 비어있지 않은 문자열 리스트여야 합니다.")
    command = tuple(command_node)

    adopt_command_node = node.get("adopt_command")
    if adopt_command_node is None:
        adopt_command = None
    elif (
        isinstance(adopt_command_node, list)
        and adopt_command_node
        and all(isinstance(c, str) and c for c in adopt_command_node)
    ):
        adopt_command = tuple(adopt_command_node)
    else:
        raise ConfigError(f"{where}.adopt_command는 비어있지 않은 문자열 리스트여야 합니다.")
    adopt_match = str(node.get("adopt_match", "exact"))
    if adopt_match not in {"exact", "prefix"}:
        raise ConfigError(f"{where}.adopt_match는 exact 또는 prefix여야 합니다.")
    if adopt_match == "prefix" and (adopt_command is None or len(adopt_command) < 2):
        raise ConfigError(f"{where}.adopt_match=prefix는 2개 이상 토큰의 adopt_command가 필요합니다.")

    env_node = node.get("env") or {}
    if not isinstance(env_node, dict) or not all(
        isinstance(k, str) and isinstance(v, (str, int, float)) for k, v in env_node.items()
    ):
        raise ConfigError(f"{where}.env는 문자열 key/value의 mapping이어야 합니다.")
    env = {k: str(v) for k, v in env_node.items()}

    port = node.get("port")
    if port is not None and (not isinstance(port, int) or isinstance(port, bool)):
        raise ConfigError(f"{where}.port는 정수여야 합니다.")

    port_env_name = node.get("port_env_name")
    if port_env_name is not None and (
        not isinstance(port_env_name, str)
        or not port_env_name
        or not all(c.isalnum() or c == "_" for c in port_env_name)
    ):
        raise ConfigError(
            f"{where}.port_env_name은 영문/숫자/_로만 구성된 문자열이어야 합니다."
        )

    open_url = node.get("open_url")
    if open_url is not None and not isinstance(open_url, str):
        raise ConfigError(f"{where}.open_url은 문자열이어야 합니다.")

    https = _parse_https(node.get("https"), where)
    health = _parse_health(node.get("health"), where)
    log = _parse_log(node.get("log"), where)
    lifecycle = _parse_lifecycle(node.get("lifecycle"), where)
    actions = _parse_actions(node.get("actions"), where)

    return ServiceConfig(
        id=sid,
        name=name,
        description=description,
        cwd=cwd,
        entry_file=entry_file,
        command=command,
        env=env,
        port=port,
        port_env_name=port_env_name,
        open_url=open_url,
        health=health,
        log=log,
        lifecycle=lifecycle,
        actions=actions,
        https=https,
        adopt_command=adopt_command,
        adopt_match=adopt_match,
    )


def _parse_https(node: Any, where: str) -> HttpsConfig:
    if node is None:
        return _default_https_config()
    if not isinstance(node, dict):
        raise ConfigError(f"{where}.https는 mapping이어야 합니다.")

    enabled = bool(node.get("enabled", False))
    cert_file = _optional_str(node.get("cert_file"), f"{where}.https.cert_file")
    key_file = _optional_str(node.get("key_file"), f"{where}.https.key_file")

    env_node = node.get("env") or {}
    if not isinstance(env_node, dict):
        raise ConfigError(f"{where}.https.env는 mapping이어야 합니다.")

    return HttpsConfig(
        enabled=enabled,
        cert_file=cert_file,
        key_file=key_file,
        enabled_env_name=_env_name(
            env_node.get("enabled"),
            "HTTPS",
            f"{where}.https.env.enabled",
        ),
        cert_file_env_name=_env_name(
            env_node.get("cert_file"),
            "SSL_CERT_FILE",
            f"{where}.https.env.cert_file",
        ),
        key_file_env_name=_env_name(
            env_node.get("key_file"),
            "SSL_KEY_FILE",
            f"{where}.https.env.key_file",
        ),
    )


def _parse_health(node: Any, where: str) -> HealthConfig:
    if node is None:
        return HealthConfig(
            enabled=False, type="none", url=None, timeout_seconds=2.0, verify_ssl=True
        )
    if not isinstance(node, dict):
        raise ConfigError(f"{where}.health는 mapping이어야 합니다.")

    enabled = bool(node.get("enabled", False))
    htype = str(node.get("type", "none"))
    url = node.get("url")
    if url is not None and not isinstance(url, str):
        raise ConfigError(f"{where}.health.url은 문자열이어야 합니다.")
    try:
        timeout = float(node.get("timeout_seconds", 2.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}.health.timeout_seconds는 숫자여야 합니다.") from exc
    verify_ssl = bool(node.get("verify_ssl", True))

    if enabled and htype == "http" and not url:
        raise ConfigError(f"{where}.health.type=http일 때 url이 필요합니다.")
    if enabled and htype == "tcp" and not url:
        raise ConfigError(
            f"{where}.health.type=tcp일 때 url(host:port)이 필요합니다."
        )
    if enabled and htype not in {"http", "tcp"}:
        raise ConfigError(f"{where}.health.type은 enabled일 때 http 또는 tcp여야 합니다.")
    if not 0 < timeout <= 30:
        raise ConfigError(f"{where}.health.timeout_seconds는 0초 초과 30초 이하여야 합니다.")

    return HealthConfig(
        enabled=enabled,
        type=htype,
        url=url,
        timeout_seconds=timeout,
        verify_ssl=verify_ssl,
    )


def _validate_network_config(
    controller: ControllerConfig, services: tuple[ServiceConfig, ...]
) -> None:
    if not 1 <= controller.port <= 65535:
        raise ConfigError("controller.port는 1..65535 범위여야 합니다.")
    seen: dict[int, str] = {}
    for service in services:
        if service.port is None:
            continue
        if not 1 <= service.port <= 65535:
            raise ConfigError(f"서비스 {service.id!r}의 port는 1..65535 범위여야 합니다.")
        if service.port == controller.port:
            raise ConfigError(
                f"서비스 {service.id!r}의 port가 controller.port와 충돌합니다: {service.port}"
            )
        previous = seen.get(service.port)
        if previous is not None:
            raise ConfigError(
                f"서비스 port 중복: {previous!r}와 {service.id!r}가 {service.port}를 사용합니다."
            )
        seen[service.port] = service.id


def _optional_str(value: Any, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{where}은 문자열이어야 합니다.")
    return value


def _env_name(value: Any, default: str, where: str) -> str:
    if value is None:
        return default
    if (
        not isinstance(value, str)
        or not value
        or not all(c.isalnum() or c == "_" for c in value)
    ):
        raise ConfigError(f"{where}은 영문/숫자/_로만 구성된 문자열이어야 합니다.")
    return value


def _parse_log(node: Any, where: str) -> LogConfig:
    if node is None:
        return LogConfig(enabled=True, tail_lines=200, max_bytes=5 * 1024 * 1024, keep=3)
    if not isinstance(node, dict):
        raise ConfigError(f"{where}.log는 mapping이어야 합니다.")
    enabled = bool(node.get("enabled", True))
    tail_lines = int(node.get("tail_lines", 200))
    if tail_lines < 0:
        raise ConfigError(f"{where}.log.tail_lines는 0 이상이어야 합니다.")
    max_bytes = int(node.get("max_bytes", 5 * 1024 * 1024))
    if max_bytes < 0:
        raise ConfigError(f"{where}.log.max_bytes는 0 이상이어야 합니다.")
    keep = int(node.get("keep", 3))
    if keep < 0:
        raise ConfigError(f"{where}.log.keep는 0 이상이어야 합니다.")
    return LogConfig(enabled=enabled, tail_lines=tail_lines, max_bytes=max_bytes, keep=keep)


def _parse_lifecycle(node: Any, where: str) -> LifecycleConfig:
    if node is None:
        return LifecycleConfig(
            mode="manual",
            autostart=False,
            stop_visibility="primary",
            restart_visibility="primary",
            unmanaged_policy="manage",
        )
    if not isinstance(node, dict):
        raise ConfigError(f"{where}.lifecycle는 mapping이어야 합니다.")

    mode = str(node.get("mode", "manual"))
    if mode not in ALLOWED_LIFECYCLE_MODES:
        raise ConfigError(
            f"{where}.lifecycle.mode는 {sorted(ALLOWED_LIFECYCLE_MODES)} 중 하나여야 합니다: {mode!r}"
        )

    autostart = bool(node.get("autostart", False))

    stop_visibility = str(node.get("stop_visibility", "primary"))
    if stop_visibility not in ALLOWED_VISIBILITY:
        raise ConfigError(
            f"{where}.lifecycle.stop_visibility는 {sorted(ALLOWED_VISIBILITY)} 중 하나여야 합니다."
        )

    restart_visibility = str(node.get("restart_visibility", "primary"))
    if restart_visibility not in ALLOWED_VISIBILITY:
        raise ConfigError(
            f"{where}.lifecycle.restart_visibility는 {sorted(ALLOWED_VISIBILITY)} 중 하나여야 합니다."
        )

    unmanaged_policy = str(node.get("unmanaged_policy", "manage"))
    if unmanaged_policy not in ALLOWED_UNMANAGED_POLICY:
        raise ConfigError(
            f"{where}.lifecycle.unmanaged_policy는 {sorted(ALLOWED_UNMANAGED_POLICY)} 중 하나여야 합니다."
        )

    return LifecycleConfig(
        mode=mode,
        autostart=autostart,
        stop_visibility=stop_visibility,
        restart_visibility=restart_visibility,
        unmanaged_policy=unmanaged_policy,
    )


def _parse_actions(node: Any, where: str) -> tuple[ActionConfig, ...]:
    if node is None:
        return ()
    if not isinstance(node, list):
        raise ConfigError(f"{where}.actions는 리스트여야 합니다.")

    seen: set[str] = set()
    out: list[ActionConfig] = []
    for i, item in enumerate(node):
        action_where = f"{where}.actions[{i}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{action_where}는 mapping이어야 합니다.")

        aid = _require_str(item, f"{action_where}.id")
        if aid in seen:
            raise ConfigError(f"{where} 안에서 action id 중복: {aid!r}")
        seen.add(aid)

        label = _require_str(item, f"{action_where}.label")
        atype = _require_str(item, f"{action_where}.type")
        if atype not in ALLOWED_ACTION_TYPES:
            raise ConfigError(
                f"{action_where}.type은 {sorted(ALLOWED_ACTION_TYPES)} 중 하나여야 합니다: {atype!r}"
            )
        enabled = bool(item.get("enabled", True))

        stop_strategy: StopStrategy | None = None
        if atype == "process_stop":
            stop_strategy = _parse_stop_strategy(item.get("strategy"), action_where)

        out.append(
            ActionConfig(
                id=aid,
                label=label,
                type=atype,
                enabled=enabled,
                stop_strategy=stop_strategy,
            )
        )

    return tuple(out)


def _parse_stop_strategy(node: Any, where: str) -> StopStrategy:
    if node is None:
        return StopStrategy(
            signal="SIGINT",
            timeout_seconds=5.0,
            confirm_required=False,
            fallback=("SIGTERM", "SIGKILL"),
        )
    if not isinstance(node, dict):
        raise ConfigError(f"{where}.strategy는 mapping이어야 합니다.")

    sig = str(node.get("signal", "SIGINT"))
    timeout = float(node.get("timeout_seconds", 5.0))
    if timeout <= 0:
        raise ConfigError(f"{where}.strategy.timeout_seconds는 0보다 커야 합니다.")
    confirm_required = bool(node.get("confirm_required", False))

    fallback_node = node.get("fallback") or []
    if not isinstance(fallback_node, list) or not all(isinstance(s, str) for s in fallback_node):
        raise ConfigError(f"{where}.strategy.fallback은 문자열 리스트여야 합니다.")

    return StopStrategy(
        signal=sig,
        timeout_seconds=timeout,
        confirm_required=confirm_required,
        fallback=tuple(fallback_node),
    )


# ----------------------------------------------------------------------
# Action(일회성 명령) 파서
# ----------------------------------------------------------------------


def _parse_action_groups(node: Any) -> tuple[ActionGroupConfig, ...]:
    """root level `actions` 섹션을 파싱한다.

    `services[].actions`(서비스 액션)와 다른 별개의 섹션이다.
    """
    if node is None:
        return ()
    if not isinstance(node, list):
        raise ConfigError("`actions`는 리스트여야 합니다.")

    seen_ids: set[str] = set()
    groups: list[ActionGroupConfig] = []
    for index, item in enumerate(node):
        group = _parse_action_group(item, index)
        if group.id in seen_ids:
            raise ConfigError(f"action group id 중복: {group.id!r}")
        seen_ids.add(group.id)
        groups.append(group)
    return tuple(groups)


def _parse_action_group(node: Any, index: int) -> ActionGroupConfig:
    where = f"actions[{index}]"
    if not isinstance(node, dict):
        raise ConfigError(f"{where}는 mapping이어야 합니다.")

    gid = _require_str(node, f"{where}.id")
    if not _is_valid_id(gid):
        raise ConfigError(
            f"{where}.id는 영문/숫자/`_`/`-`로만 구성되어야 합니다: {gid!r}"
        )

    name = _require_str(node, f"{where}.name")
    description = str(node.get("description", ""))

    items_node = node.get("items")
    if not isinstance(items_node, list) or not items_node:
        raise ConfigError(f"{where}.items는 비어있지 않은 리스트여야 합니다.")

    seen_item_ids: set[str] = set()
    items: list[ActionItemConfig] = []
    for i, item_node in enumerate(items_node):
        action_item = _parse_action_item(item_node, f"{where}.items[{i}]")
        if action_item.id in seen_item_ids:
            raise ConfigError(f"{where} 안에서 action item id 중복: {action_item.id!r}")
        seen_item_ids.add(action_item.id)
        items.append(action_item)

    return ActionGroupConfig(
        id=gid,
        name=name,
        description=description,
        items=tuple(items),
    )


def _parse_action_item(node: Any, where: str) -> ActionItemConfig:
    if not isinstance(node, dict):
        raise ConfigError(f"{where}는 mapping이어야 합니다.")

    aid = _require_str(node, f"{where}.id")
    if not _is_valid_id(aid):
        raise ConfigError(
            f"{where}.id는 영문/숫자/`_`/`-`로만 구성되어야 합니다: {aid!r}"
        )

    name = _require_str(node, f"{where}.name")
    description = str(node.get("description", ""))
    kind = _require_str(node, f"{where}.kind")
    if kind not in ALLOWED_ACTION_KINDS:
        raise ConfigError(
            f"{where}.kind는 {sorted(ALLOWED_ACTION_KINDS)} 중 하나여야 합니다: {kind!r}"
        )

    command_node = node.get("command")
    if (
        not isinstance(command_node, list)
        or not command_node
        or not all(isinstance(c, str) and c for c in command_node)
    ):
        raise ConfigError(
            f"{where}.command는 비어있지 않은 문자열 리스트여야 합니다."
        )
    command = tuple(command_node)

    cwd: str | None
    if kind == "python":
        cwd = _require_str(node, f"{where}.cwd")
    else:
        cwd_value = node.get("cwd")
        if cwd_value is not None and (not isinstance(cwd_value, str) or not cwd_value):
            raise ConfigError(f"{where}.cwd는 비어있지 않은 문자열이어야 합니다.")
        cwd = cwd_value

    # argv kind에 deny list 적용. python kind는 인터프리터+스크립트 형태로
    # 사실상 sandbox가 없지만, 의도한 사용 패턴(파이썬 스크립트 실행)을 깨지 않도록
    # rm/sudo 같은 시스템 명령 차단만 적용한다.
    validate_command_safety(command, kind=kind, where=where)

    env_node = node.get("env") or {}
    if not isinstance(env_node, dict) or not all(
        isinstance(k, str) and isinstance(v, (str, int, float)) for k, v in env_node.items()
    ):
        raise ConfigError(f"{where}.env는 문자열 key/value의 mapping이어야 합니다.")
    env = {k: str(v) for k, v in env_node.items()}

    log = _parse_action_run_log(node.get("log"), where)
    external_logs = _parse_external_logs(node.get("external_logs"), where)

    return ActionItemConfig(
        id=aid,
        name=name,
        description=description,
        kind=kind,
        cwd=cwd,
        command=command,
        env=env,
        log=log,
        external_logs=external_logs,
    )


def _parse_action_run_log(node: Any, where: str) -> ActionRunLogConfig:
    if node is None:
        return ActionRunLogConfig(enabled=True, keep_runs=20)
    if not isinstance(node, dict):
        raise ConfigError(f"{where}.log는 mapping이어야 합니다.")
    enabled = bool(node.get("enabled", True))
    keep_runs = int(node.get("keep_runs", 20))
    if keep_runs < 0:
        raise ConfigError(f"{where}.log.keep_runs는 0 이상이어야 합니다.")
    return ActionRunLogConfig(enabled=enabled, keep_runs=keep_runs)


def _parse_external_logs(node: Any, where: str) -> tuple[ExternalLogConfig, ...]:
    if node is None:
        return ()
    if not isinstance(node, list):
        raise ConfigError(f"{where}.external_logs는 리스트여야 합니다.")
    out: list[ExternalLogConfig] = []
    for i, item in enumerate(node):
        sub_where = f"{where}.external_logs[{i}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{sub_where}는 mapping이어야 합니다.")
        name = _require_str(item, f"{sub_where}.name")
        path = _require_str(item, f"{sub_where}.path")
        out.append(ExternalLogConfig(name=name, path=path))
    return tuple(out)


def validate_command_safety(
    command: tuple[str, ...] | list[str],
    *,
    kind: str,
    where: str,
) -> None:
    """ActionItem command를 deny list 기준으로 검증한다.

    - basename으로 비교하여 절대경로/상대경로 우회를 막는다.
    - 두 kind 공통: ACTION_DENY_BASENAMES (sudo, rm 등 시스템 위험 명령) 차단.
    - 두 kind 공통: ACTION_SHELL_BASENAMES 단독 실행 차단 (bash -c 같은 임의 셸).
    - 두 kind 공통: EVAL 인터프리터(`python`, `ruby`, `node`) + 첫 인자 `-c`/`-e` 차단.
      kind=python의 정상 사용은 `python -u script.py`이므로 -c/-e만 막으면 충분.

    설계 메모:
    - 초기에는 kind=python에 인터프리터 차단을 풀려 했지만, UI에서 kind를 자유롭게
      선택할 수 있어 kind=python이 deny list 우회로가 된다. 두 kind에 동일 정책 적용.

    등록 시점과 실행 시점에서 모두 호출하기 위해 외부에 노출한다.
    """
    if not command:
        raise ConfigError(f"{where}.command가 비어있습니다.")

    head = command[0]
    if not head:
        raise ConfigError(f"{where}.command[0]이 비어있습니다.")

    head_base = os.path.basename(head)

    # 시스템 위험 명령은 두 kind 모두 거부.
    if head_base in ACTION_DENY_BASENAMES:
        raise ConfigError(
            f"{where}.command: {head_base!r}는 차단된 명령입니다."
        )

    # 셸 단독 실행 + -c/-e 평가 인터프리터 차단도 두 kind 모두에 적용.
    if head_base in ACTION_SHELL_BASENAMES:
        raise ConfigError(
            f"{where}.command: 셸({head_base!r}) 실행은 허용되지 않습니다. "
            "스크립트를 직접 실행하세요."
        )
    if head_base in ACTION_EVAL_BASENAMES:
        second = command[1] if len(command) > 1 else ""
        if second in {"-c", "-e"}:
            raise ConfigError(
                f"{where}.command: 인터프리터({head_base!r}) {second} 평가는 차단됩니다."
            )


def _require_str(node: dict[str, Any], key_path: str) -> str:
    key = key_path.split(".")[-1]
    value = node.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key_path}는 비어있지 않은 문자열이어야 합니다.")
    return value


def _require_int(node: dict[str, Any], key_path: str) -> int:
    key = key_path.split(".")[-1]
    value = node.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{key_path}는 정수여야 합니다.")
    return value


def _is_valid_id(value: str) -> bool:
    if not value:
        return False
    return all(c.isalnum() or c in {"_", "-"} for c in value)
