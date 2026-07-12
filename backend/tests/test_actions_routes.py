"""Action API 라우트 통합 테스트."""

from __future__ import annotations

import time
from pathlib import Path
from textwrap import dedent

import pytest

import app as backend_app
from auth import PASSWORD_ENV


def _seed_config(path: Path, *, allowed_root: str) -> None:
    path.write_text(
        dedent(
            f"""
            controller:
              host: "127.0.0.1"
              port: 9000
              allowed_path_roots:
                - "{allowed_root}"
            services: []
            actions:
              - id: "demo"
                name: "Demo"
                items:
                  - id: "echo_hi"
                    name: "Echo"
                    kind: "argv"
                    command: ["echo", "hi"]
                    log:
                      enabled: true
                      keep_runs: 3
            """
        ).strip(),
        encoding="utf-8",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yml"
    _seed_config(config_path, allowed_root=str(tmp_path))
    monkeypatch.setenv(PASSWORD_ENV, "pw")

    app = backend_app.create_app(
        config_path=config_path,
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


def test_list_actions(client):
    res = client.get("/api/actions")
    assert res.status_code == 200
    body = res.get_json()
    assert "groups" in body
    assert body["groups"][0]["id"] == "demo"
    items = body["groups"][0]["items"]
    assert items[0]["id"] == "echo_hi"
    assert items[0]["recent_runs"] == []


def test_run_and_inspect(client):
    csrf = _csrf(client)
    res = client.post(
        "/api/actions/demo/echo_hi/run",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    body = res.get_json()
    run_id = body["run"]["run_id"]
    assert body["run"]["status"] in {"running", "succeeded"}

    # 자식이 끝날 때까지 잠깐 대기
    deadline = time.time() + 5.0
    final_status = None
    while time.time() < deadline:
        time.sleep(0.05)
        r = client.get(f"/api/actions/runs/{run_id}")
        if r.status_code == 200:
            run_payload = r.get_json()["run"]
            if run_payload["status"] != "running":
                final_status = run_payload["status"]
                break
    assert final_status == "succeeded"

    r = client.get(f"/api/actions/runs/{run_id}")
    body = r.get_json()
    assert "hi" in "\n".join(body["lines"])


def test_run_tail_zero_returns_metadata_without_lines(client):
    csrf = _csrf(client)
    res = client.post(
        "/api/actions/demo/echo_hi/run",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    run_id = res.get_json()["run"]["run_id"]

    deadline = time.time() + 5.0
    while time.time() < deadline:
        time.sleep(0.05)
        r = client.get(f"/api/actions/runs/{run_id}?tail=0")
        assert r.status_code == 200
        body = r.get_json()
        assert body["lines"] == []
        if body["run"]["status"] != "running":
            break
    else:
        raise AssertionError("run did not finish")


def test_run_unknown_group(client):
    csrf = _csrf(client)
    res = client.post(
        "/api/actions/nope/x/run",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 404
    assert res.get_json()["error"] == "action_group_not_found"


def test_create_action_group_via_api(client):
    csrf = _csrf(client)
    payload = {
        "id": "disk",
        "name": "Disk",
        "items": [
            {
                "id": "ls_root",
                "name": "ls /",
                "kind": "argv",
                "command": ["ls", "/"],
            }
        ],
    }
    res = client.post(
        "/api/config/actions",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.get_json()

    res = client.get("/api/actions")
    ids = [g["id"] for g in res.get_json()["groups"]]
    assert "disk" in ids


def test_create_action_group_blocks_sudo(client):
    csrf = _csrf(client)
    payload = {
        "id": "evil",
        "name": "Evil",
        "items": [
            {
                "id": "x",
                "name": "x",
                "kind": "argv",
                "command": ["sudo", "ls"],
            }
        ],
    }
    res = client.post(
        "/api/config/actions",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 400
    body = res.get_json()
    assert body["error"] == "command_blocked"


def test_create_action_group_blocks_python_eval(client):
    csrf = _csrf(client)
    payload = {
        "id": "evil",
        "name": "Evil",
        "items": [
            {
                "id": "x",
                "name": "x",
                "kind": "argv",
                "command": ["python", "-c", "print(1)"],
            }
        ],
    }
    res = client.post(
        "/api/config/actions",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 400


def test_create_action_group_python_kind_requires_allowed_cwd(client, tmp_path):
    csrf = _csrf(client)
    payload = {
        "id": "py_evil",
        "name": "py_evil",
        "items": [
            {
                "id": "x",
                "name": "x",
                "kind": "python",
                "cwd": "/etc",  # allowed_root는 tmp_path
                "command": ["python", "-u", "x.py"],
            }
        ],
    }
    res = client.post(
        "/api/config/actions",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 403
    assert res.get_json()["error"] == "cwd_not_allowed"


def test_delete_action_group(client):
    csrf = _csrf(client)
    res = client.delete(
        "/api/config/actions/demo",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    res = client.get("/api/actions")
    assert res.get_json()["groups"] == []


def test_run_requires_csrf(client):
    res = client.post("/api/actions/demo/echo_hi/run")
    assert res.status_code == 403
