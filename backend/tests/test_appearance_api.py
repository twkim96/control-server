from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import app as backend_app
from auth import PASSWORD_ENV


def _seed_minimal_config(path: Path) -> None:
    path.write_text(
        dedent(
            """
            controller:
              host: "127.0.0.1"
              port: 9000
              allowed_path_roots:
                - "/tmp"
            services:
              - id: "dummy"
                name: "Dummy"
                cwd: "/tmp"
                entry_file: "x.py"
                command: ["python", "x.py"]
            """
        ).strip(),
        encoding="utf-8",
    )


def _client(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yml"
    _seed_minimal_config(config_path)
    monkeypatch.setenv(PASSWORD_ENV, "secret123")
    app = backend_app.create_app(
        config_path=config_path,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "_no_dist",
    )
    app.config["TESTING"] = True
    return app.test_client(), tmp_path / "runtime" / "appearance.json"


def _login(client) -> str:
    res = client.post("/api/auth/login", json={"password": "secret123"})
    assert res.status_code == 200
    return res.get_json()["csrf_token"]


def test_get_appearance_returns_default_without_auth(tmp_path, monkeypatch):
    client, store_path = _client(tmp_path, monkeypatch)
    res = client.get("/api/settings/appearance")
    assert res.status_code == 200
    body = res.get_json()
    assert body == {
        "persisted": False,
        "settings": {
            "backgroundColor": "#0b0d10",
            "textColor": "#e7ebf0",
            "accentColor": "#3b82f6",
        },
    }
    assert not store_path.exists()


def test_update_appearance_persists_on_server(tmp_path, monkeypatch):
    client, store_path = _client(tmp_path, monkeypatch)
    csrf = _login(client)

    res = client.put(
        "/api/settings/appearance",
        json={
            "settings": {
                "backgroundColor": "#112233",
                "textColor": "#AABBCC",
                "accentColor": "#445566",
            },
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["persisted"] is True
    assert body["settings"] == {
        "backgroundColor": "#112233",
        "textColor": "#aabbcc",
        "accentColor": "#445566",
    }
    assert store_path.is_file()

    res = client.get("/api/settings/appearance")
    assert res.status_code == 200
    assert res.get_json()["settings"] == body["settings"]


def test_update_appearance_requires_csrf(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    _login(client)
    res = client.put(
        "/api/settings/appearance",
        json={"settings": {"backgroundColor": "#112233"}},
    )
    assert res.status_code == 403


def test_reset_appearance_removes_server_file(tmp_path, monkeypatch):
    client, store_path = _client(tmp_path, monkeypatch)
    csrf = _login(client)
    client.put(
        "/api/settings/appearance",
        json={"settings": {"backgroundColor": "#112233"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert store_path.exists()

    res = client.delete(
        "/api/settings/appearance",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["persisted"] is False
    assert body["settings"]["backgroundColor"] == "#0b0d10"
    assert not store_path.exists()
