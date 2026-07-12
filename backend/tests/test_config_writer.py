"""config_writer 테스트.

저장 시:
* 검증 실패하면 기존 파일이 유지되고 .bak도 만들어지지 않는다.
* 검증 성공하면 atomic replace + .bak 백업이 만들어진다.
* upsert/delete가 정상 동작한다.
"""
from __future__ import annotations

import threading
from pathlib import Path
from textwrap import dedent

import pytest

from config_loader import ConfigError, load_config
from config_writer import (
    ConfigWriteError,
    delete_service,
    save_config,
    upsert_service,
)


def _seed_config(path: Path) -> None:
    path.write_text(
        dedent(
            """
            controller:
              host: "0.0.0.0"
              port: 9000
            services:
              - id: "base_service"
                name: "Base Service"
                cwd: "/tmp"
                entry_file: "app.py"
                command: ["python", "app.py"]
            """
        ).strip(),
        encoding="utf-8",
    )


def test_save_valid_creates_backup(tmp_path: Path) -> None:
    p = tmp_path / "config.yml"
    _seed_config(p)
    raw = {
        "controller": {"host": "0.0.0.0", "port": 9000},
        "services": [
            {
                "id": "new",
                "name": "new",
                "cwd": "/tmp",
                "entry_file": "x.py",
                "command": ["python", "x.py"],
            }
        ],
    }
    save_config(p, raw)
    assert (tmp_path / "config.yml.bak").is_file()
    text = p.read_text(encoding="utf-8")
    assert "id: \"new\"" in text or "id: new" in text


def test_save_invalid_keeps_existing(tmp_path: Path) -> None:
    p = tmp_path / "config.yml"
    _seed_config(p)
    original = p.read_text(encoding="utf-8")

    bad = {
        "controller": {"host": "0.0.0.0", "port": 9000},
        "services": [
            {"id": "broken"}  # 필수 필드 부족
        ],
    }
    with pytest.raises(ConfigError):
        save_config(p, bad)

    assert p.read_text(encoding="utf-8") == original
    assert not (tmp_path / "config.yml.bak").exists()
    assert not (tmp_path / "config.yml.tmp").exists()


def test_upsert_creates_new(tmp_path: Path) -> None:
    p = tmp_path / "config.yml"
    _seed_config(p)

    new_service = {
        "id": "example_service",
        "name": "Example Service",
        "cwd": "/tmp",
        "entry_file": "server.py",
        "command": ["python", "server.py"],
    }
    cfg = upsert_service(p, new_service)
    ids = [s.id for s in cfg.services]
    assert ids == ["base_service", "example_service"]


def test_concurrent_upserts_preserve_both_services(tmp_path: Path) -> None:
    """같은 config 경로의 동시에 시작한 create가 서로의 변경을 잃지 않는다."""
    p = tmp_path / "config.yml"
    _seed_config(p)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def create(sid: str) -> None:
        try:
            barrier.wait(timeout=5)
            upsert_service(
                p,
                {
                    "id": sid,
                    "name": sid,
                    "cwd": "/tmp",
                    "entry_file": "x.py",
                    "command": ["python", "x.py"],
                },
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    left = threading.Thread(target=create, args=("left",))
    right = threading.Thread(target=create, args=("right",))
    left.start()
    right.start()
    left.join(timeout=5)
    right.join(timeout=5)

    assert errors == []
    ids = {service.id for service in load_config(p).services}
    assert ids == {"base_service", "left", "right"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_upsert_updates_existing(tmp_path: Path) -> None:
    p = tmp_path / "config.yml"
    _seed_config(p)

    updated = {
        "id": "base_service",
        "name": "Updated Base Service",
        "cwd": "/var",
        "entry_file": "app.py",
        "command": ["python", "-u", "app.py"],
    }
    cfg = upsert_service(p, updated)
    service = next(s for s in cfg.services if s.id == "base_service")
    assert service.name == "Updated Base Service"
    assert service.cwd == "/var"
    assert service.command == ("python", "-u", "app.py")


def test_delete_service(tmp_path: Path) -> None:
    p = tmp_path / "config.yml"
    _seed_config(p)
    cfg = delete_service(p, "base_service")
    assert cfg.services == ()


def test_delete_missing_raises(tmp_path: Path) -> None:
    p = tmp_path / "config.yml"
    _seed_config(p)
    with pytest.raises(ConfigWriteError):
        delete_service(p, "no_such")


def test_save_atomic_no_partial_file(tmp_path: Path) -> None:
    """검증 실패 시 .tmp 파일도 남지 않아야 한다."""
    p = tmp_path / "config.yml"
    _seed_config(p)

    bad = {"controller": "not a mapping"}
    with pytest.raises(ConfigError):
        save_config(p, bad)
    assert not (tmp_path / "config.yml.tmp").exists()



def test_full_payload_with_health_lifecycle_actions(tmp_path: Path) -> None:
    """프론트가 보내는 모양 그대로 round-trip 가능한지 확인."""
    p = tmp_path / "config.yml"
    p.write_text(
        dedent(
            """
            controller:
              host: "0.0.0.0"
              port: 9000
              allowed_path_roots:
                - "/tmp"
            services: []
            """
        ).strip(),
        encoding="utf-8",
    )

    payload = {
        "id": "newsvc",
        "name": "New Svc",
        "description": "test",
        "cwd": "/tmp",
        "entry_file": "x.py",
        "command": ["python", "-u", "x.py"],
        "env": {"FOO": "bar", "BAZ": "1"},
        "port": 7000,
        "open_url": "http://127.0.0.1:7000",
        "https": {
            "enabled": True,
            "cert_file": ".certs/dev.crt",
            "key_file": ".certs/dev.key",
            "env": {
                "enabled": "DEV_HTTPS",
                "cert_file": "DEV_CERT_FILE",
                "key_file": "DEV_KEY_FILE",
            },
        },
        "health": {
            "enabled": True,
            "type": "http",
            "url": "http://127.0.0.1:7000/health",
            "timeout_seconds": 2,
        },
        "log": {
            "enabled": True,
            "tail_lines": 200,
            "max_bytes": 5_242_880,
            "keep": 3,
        },
        "lifecycle": {
            "mode": "always_on",
            "autostart": False,
            "stop_visibility": "danger_menu",
            "restart_visibility": "primary",
            "unmanaged_policy": "status_only",
        },
        "actions": [
            {"id": "start", "label": "시작", "type": "process_start", "enabled": True},
            {
                "id": "stop",
                "label": "중지",
                "type": "process_stop",
                "enabled": True,
                "strategy": {
                    "signal": "SIGINT",
                    "timeout_seconds": 60,
                    "confirm_required": True,
                    "fallback": ["SIGTERM", "SIGKILL"],
                },
            },
        ],
    }

    cfg = upsert_service(p, payload)
    saved = next(s for s in cfg.services if s.id == "newsvc")
    assert saved.lifecycle.mode == "always_on"
    assert saved.https.enabled is True
    assert saved.https.cert_file_env_name == "DEV_CERT_FILE"
    assert saved.health.url == "http://127.0.0.1:7000/health"
    stop = next(a for a in saved.actions if a.id == "stop")
    assert stop.stop_strategy is not None
    assert stop.stop_strategy.timeout_seconds == 60.0


def test_payload_with_null_optional_fields(tmp_path: Path) -> None:
    """port나 open_url이 null인 케이스도 통과해야 한다."""
    p = tmp_path / "config.yml"
    p.write_text(
        dedent(
            """
            controller:
              host: "0.0.0.0"
              port: 9000
            services: []
            """
        ).strip(),
        encoding="utf-8",
    )

    payload = {
        "id": "minimal",
        "name": "Minimal",
        "cwd": "/tmp",
        "entry_file": "x.py",
        "command": ["python", "x.py"],
        "port": None,
        "open_url": None,
        "health": {
            "enabled": False,
            "type": "none",
            "url": None,
            "timeout_seconds": 2,
        },
    }

    cfg = upsert_service(p, payload)
    saved = next(s for s in cfg.services if s.id == "minimal")
    assert saved.port is None
    assert saved.open_url is None
    assert saved.health.enabled is False
