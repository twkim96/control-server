"""Config schema, validation, serialization, and update round-trips for supervision."""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from config_loader import (
    ConfigError,
    HealthConfig,
    LifecycleConfig,
    LogConfig,
    ServiceConfig,
    load_config,
)
from config_writer import upsert_service
from service_registry import ServiceRegistry


def _service(service_id: str = "api", **updates: Any) -> dict[str, Any]:
    service: dict[str, Any] = {
        "id": service_id,
        "name": service_id,
        "cwd": "/tmp",
        "entry_file": "app.py",
        "command": ["python", "app.py"],
    }
    service.update(updates)
    return service


def _load_services(tmp_path: Path, services: list[dict[str, Any]]):
    yaml = YAML(typ="rt")
    buffer = StringIO()
    yaml.dump(
        {
            "controller": {"host": "127.0.0.1", "port": 9000},
            "services": services,
        },
        buffer,
    )
    path = tmp_path / "config.yml"
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return load_config(path)


def test_legacy_service_uses_supervision_defaults_and_old_constructors_still_work(
    tmp_path: Path,
) -> None:
    service = _load_services(tmp_path, [_service()]).services[0]
    assert service.restart.autorestart is True
    assert service.restart.min_uptime_seconds == 5.0
    assert service.restart.max_restarts == 5
    assert service.restart.restart_delay_ms == 1000
    assert service.restart.exp_backoff_restart_delay_ms == 100
    assert service.restart.max_memory_restart_mb == 0
    assert service.recovery.enabled is False
    assert service.recovery.failure_threshold == 3
    assert service.recovery.startup_grace_seconds == 30.0
    assert service.recovery.cooldown_seconds == 300.0
    assert service.recovery.max_restarts_per_hour == 3
    assert service.group == ""
    assert service.depends_on == ()
    assert service.log.rotate_while_running is False
    assert service.log.compress is False

    legacy_log = LogConfig(True, 200, 0, 0)
    legacy_service = ServiceConfig(
        "legacy",
        "Legacy",
        "",
        "/tmp",
        "app.py",
        ("python", "app.py"),
        {},
        None,
        None,
        None,
        HealthConfig(False, "none", None, 2.0, True),
        legacy_log,
        LifecycleConfig("manual", False, "primary", "primary", "manage"),
        (),
    )
    assert legacy_service.restart.autorestart is True
    assert legacy_service.recovery.enabled is False
    assert legacy_service.group == ""
    assert legacy_service.depends_on == ()


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("restart", "autorestart", "false", "autorestart.*boolean"),
        ("restart", "min_uptime_seconds", 0, "min_uptime_seconds.*0보다"),
        ("restart", "min_uptime_seconds", -0.1, "min_uptime_seconds.*0보다"),
        ("restart", "min_uptime_seconds", float("nan"), "유한한 숫자"),
        ("restart", "min_uptime_seconds", float("inf"), "유한한 숫자"),
        ("restart", "min_uptime_seconds", "5", "유한한 숫자"),
        ("restart", "max_restarts", 0, "max_restarts.*1 이상"),
        ("restart", "max_restarts", True, "max_restarts.*정수"),
        ("restart", "restart_delay_ms", -1, "restart_delay_ms.*0 이상"),
        ("restart", "restart_delay_ms", 1.5, "restart_delay_ms.*정수"),
        ("restart", "exp_backoff_restart_delay_ms", -1, "exp_backoff_restart_delay_ms.*0 이상"),
        ("restart", "max_memory_restart_mb", -1, "max_memory_restart_mb.*0 이상"),
        ("recovery", "enabled", "true", "recovery.enabled.*boolean"),
        ("recovery", "failure_threshold", 0, "failure_threshold.*1 이상"),
        ("recovery", "failure_threshold", True, "failure_threshold.*정수"),
        ("recovery", "startup_grace_seconds", -1, "startup_grace_seconds.*0 이상"),
        ("recovery", "startup_grace_seconds", float("inf"), "유한한 숫자"),
        ("recovery", "cooldown_seconds", -1, "cooldown_seconds.*0 이상"),
        ("recovery", "max_restarts_per_hour", 0, r"max_restarts_per_hour.*1\.\.100"),
        ("recovery", "max_restarts_per_hour", 1.0, "max_restarts_per_hour.*정수"),
    ],
)
def test_invalid_restart_and_recovery_values_are_rejected(
    tmp_path: Path, section: str, field: str, value: Any, message: str
) -> None:
    service = _service()
    service[section] = {field: value}
    with pytest.raises(ConfigError, match=message):
        _load_services(tmp_path, [service])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rotate_while_running", "true"),
        ("compress", 1),
    ],
)
def test_new_log_flags_require_boolean_values(
    tmp_path: Path, field: str, value: Any
) -> None:
    service = _service(log={field: value})
    with pytest.raises(ConfigError, match=field + ".*boolean"):
        _load_services(tmp_path, [service])


def test_recovery_requires_enabled_health_check(tmp_path: Path) -> None:
    service = _service(recovery={"enabled": True})
    with pytest.raises(ConfigError, match="recovery.enabled.*health.enabled=true"):
        _load_services(tmp_path, [service])


def test_dependencies_allow_forward_references_and_reject_invalid_graphs(
    tmp_path: Path,
) -> None:
    cfg = _load_services(
        tmp_path,
        [_service("api", depends_on=["database"]), _service("database")],
    )
    assert cfg.services[0].depends_on == ("database",)

    cases = [
        ([_service("api", depends_on=["missing"])], "알 수 없는 depends_on"),
        ([_service("api", depends_on=["api"])], "자기 자신"),
        ([_service("api", depends_on=["database", "database"]), _service("database")], "중복 참조"),
        (
            [_service("api", depends_on=["database"]), _service("database", depends_on=["api"])],
            "순환 참조",
        ),
    ]
    for services, message in cases:
        with pytest.raises(ConfigError, match=message):
            _load_services(tmp_path, services)


def test_service_meta_contains_exact_supervision_and_log_fields(tmp_path: Path) -> None:
    service_input = _service(
        "api",
        group="backend",
        depends_on=["database"],
        restart={
            "autorestart": False,
            "min_uptime_seconds": 8.5,
            "max_restarts": 9,
            "restart_delay_ms": 2000,
            "exp_backoff_restart_delay_ms": 250,
            "max_memory_restart_mb": 512,
        },
        recovery={
            "enabled": True,
            "failure_threshold": 4,
            "startup_grace_seconds": 45,
            "cooldown_seconds": 600,
            "max_restarts_per_hour": 2,
        },
        health={"enabled": True, "type": "http", "url": "http://127.0.0.1/health"},
        log={"rotate_while_running": True, "compress": True},
    )
    cfg = _load_services(tmp_path, [service_input, _service("database")])
    meta = ServiceRegistry(cfg).service_to_meta(cfg.services[0])

    assert meta["restart"] == {
        "autorestart": False,
        "min_uptime_seconds": 8.5,
        "max_restarts": 9,
        "restart_delay_ms": 2000,
        "exp_backoff_restart_delay_ms": 250,
        "max_memory_restart_mb": 512,
    }
    assert meta["recovery"] == {
        "enabled": True,
        "failure_threshold": 4,
        "startup_grace_seconds": 45.0,
        "cooldown_seconds": 600.0,
        "max_restarts_per_hour": 2,
    }
    assert meta["group"] == "backend"
    assert meta["depends_on"] == ["database"]
    assert meta["log"]["rotate_while_running"] is True
    assert meta["log"]["compress"] is True


def test_upsert_service_round_trips_supervision_fields_on_update(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    initial = {
        "controller": {"host": "127.0.0.1", "port": 9000},
        "services": [_service("database"), _service("api")],
    }
    yaml = YAML(typ="rt")
    with path.open("w", encoding="utf-8") as stream:
        yaml.dump(initial, stream)

    updated = _service(
        "api",
        group="backend",
        depends_on=["database"],
        restart={"autorestart": False, "max_memory_restart_mb": 256},
        recovery={"enabled": False, "cooldown_seconds": 900},
        log={"rotate_while_running": True, "compress": True},
    )
    saved = upsert_service(path, updated)
    reloaded = load_config(path)
    for config in (saved, reloaded):
        api = next(service for service in config.services if service.id == "api")
        assert api.group == "backend"
        assert api.depends_on == ("database",)
        assert api.restart.autorestart is False
        assert api.restart.max_memory_restart_mb == 256
        assert api.recovery.cooldown_seconds == 900.0
        assert api.log.rotate_while_running is True
        assert api.log.compress is True


@pytest.mark.parametrize("update", [{"group": "a/b"}, {"group": " trailing "}, {"recovery": {"max_restarts_per_hour": 101}}])
def test_operational_name_and_budget_bounds(tmp_path, update):
    from config_loader import _parse_service, ConfigError
    payload = {"id": "one", "name": "One", "cwd": str(tmp_path), "entry_file": "s.py", "command": ["python", "s.py"], **update}
    with pytest.raises(ConfigError):
        _parse_service(payload, 0)
