"""순서 변경 API와 config_writer reorder 함수 테스트."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

import app as backend_app
from auth import PASSWORD_ENV
from config_loader import load_config
from config_writer import (
    ConfigWriteError,
    reorder_action_groups,
    reorder_services,
)


def _seed_config(path: Path, *, allowed_root: str) -> None:
    path.write_text(
        dedent(
            f"""
            controller:
              host: "127.0.0.1"
              port: 9000
              allowed_path_roots:
                - "{allowed_root}"
            services:
              - id: "alpha"
                name: "Alpha"
                cwd: "/tmp"
                entry_file: "x.py"
                command: ["python", "x.py"]
              - id: "beta"
                name: "Beta"
                cwd: "/tmp"
                entry_file: "x.py"
                command: ["python", "x.py"]
              - id: "gamma"
                name: "Gamma"
                cwd: "/tmp"
                entry_file: "x.py"
                command: ["python", "x.py"]
            actions:
              - id: "g1"
                name: "G1"
                items:
                  - id: "a"
                    name: "A"
                    kind: "argv"
                    command: ["echo", "a"]
              - id: "g2"
                name: "G2"
                items:
                  - id: "b"
                    name: "B"
                    kind: "argv"
                    command: ["echo", "b"]
            """
        ).strip(),
        encoding="utf-8",
    )


# ----------------------------------------------------------------------
# config_writer 단위
# ----------------------------------------------------------------------


def test_reorder_services_unit(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    _seed_config(cfg, allowed_root="/tmp")
    new_cfg = reorder_services(cfg, ["gamma", "alpha", "beta"])
    assert [s.id for s in new_cfg.services] == ["gamma", "alpha", "beta"]
    # round-trip: 다시 읽어도 같은 순서.
    re_read = load_config(cfg)
    assert [s.id for s in re_read.services] == ["gamma", "alpha", "beta"]


def test_reorder_action_groups_unit(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    _seed_config(cfg, allowed_root="/tmp")
    new_cfg = reorder_action_groups(cfg, ["g2", "g1"])
    assert [g.id for g in new_cfg.action_groups] == ["g2", "g1"]


def test_reorder_rejects_missing_id(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    _seed_config(cfg, allowed_root="/tmp")
    with pytest.raises(ConfigWriteError, match="missing"):
        reorder_services(cfg, ["alpha", "beta"])  # gamma 빠짐


def test_reorder_rejects_unknown_id(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    _seed_config(cfg, allowed_root="/tmp")
    with pytest.raises(ConfigWriteError, match="unknown"):
        reorder_services(cfg, ["alpha", "beta", "gamma", "delta"])


def test_reorder_rejects_duplicate(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    _seed_config(cfg, allowed_root="/tmp")
    with pytest.raises(ConfigWriteError, match="중복"):
        reorder_services(cfg, ["alpha", "alpha", "beta"])


# ----------------------------------------------------------------------
# 라우트 통합
# ----------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yml"
    _seed_config(cfg, allowed_root=str(tmp_path))
    monkeypatch.setenv(PASSWORD_ENV, "pw")

    app = backend_app.create_app(
        config_path=cfg,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "_no_dist",
    )
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/api/auth/login", json={"password": "pw"})
    return c


def _csrf(c) -> str:
    return c.get("/api/auth/me").get_json()["csrf_token"]


def test_reorder_services_route(client):
    csrf = _csrf(client)
    res = client.post(
        "/api/config/services/reorder",
        json={"order": ["beta", "gamma", "alpha"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.get_json()
    services = client.get("/api/services").get_json()["services"]
    assert [s["id"] for s in services] == ["beta", "gamma", "alpha"]


def test_reorder_actions_route(client):
    csrf = _csrf(client)
    res = client.post(
        "/api/config/actions/reorder",
        json={"order": ["g2", "g1"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.get_json()
    groups = client.get("/api/actions").get_json()["groups"]
    assert [g["id"] for g in groups] == ["g2", "g1"]


def test_reorder_route_rejects_missing_id(client):
    csrf = _csrf(client)
    res = client.post(
        "/api/config/services/reorder",
        json={"order": ["alpha", "beta"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 400


def test_reorder_route_requires_csrf(client):
    res = client.post(
        "/api/config/services/reorder",
        json={"order": ["alpha", "beta", "gamma"]},
    )
    assert res.status_code == 403


def test_reorder_invalid_body(client):
    csrf = _csrf(client)
    res = client.post(
        "/api/config/services/reorder",
        json={"order": "not-a-list"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 400


# ----------------------------------------------------------------------
# ActionGroup PUT으로 items 순서 보존
# ----------------------------------------------------------------------


def test_action_group_put_preserves_item_order(client):
    csrf = _csrf(client)
    # g1에 새로운 items 순서로 PUT
    payload = {
        "id": "g1",
        "name": "G1",
        "items": [
            {"id": "a", "name": "A", "kind": "argv", "command": ["echo", "a"]},
            {"id": "c", "name": "C", "kind": "argv", "command": ["echo", "c"]},
            {"id": "b2", "name": "B2", "kind": "argv", "command": ["echo", "b2"]},
        ],
    }
    res = client.put(
        "/api/config/actions/g1",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.get_json()

    groups = client.get("/api/actions").get_json()["groups"]
    g1 = next(g for g in groups if g["id"] == "g1")
    assert [it["id"] for it in g1["items"]] == ["a", "c", "b2"]
