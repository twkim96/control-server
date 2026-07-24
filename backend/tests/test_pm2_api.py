from __future__ import annotations

import json
import os
from pathlib import Path
from textwrap import dedent

import app as backend_app
from auth import PASSWORD_ENV
from pm2_manager import CommandResult


class FakePm2:
    def __init__(self) -> None:
        self.status: str | None = None
        self.commands: list[list[str]] = []

    def __call__(self, argv, _env, _timeout):
        command = argv[1]
        self.commands.append(argv[1:])
        if command in {"start", "startOrRestart"}:
            self.status = "online"
            return CommandResult(0, "ok", "")
        if command == "stop":
            self.status = "stopped"
            return CommandResult(0, "ok", "")
        if command == "delete":
            self.status = None
            return CommandResult(0, "ok", "")
        if command == "jlist":
            items = []
            if self.status is not None:
                items.append(
                    {
                        "name": "server-control--dummy",
                        "pid": os.getpid() if self.status == "online" else 0,
                        "pm2_env": {
                            "status": self.status,
                            "pm_uptime": 1_700_000_000_000,
                            "restart_time": 2,
                            "exit_code": 0,
                        },
                    }
                )
            return CommandResult(0, json.dumps(items, separators=(",", ":")), "")
        raise AssertionError(f"unexpected PM2 command: {command}")


def _config(path: Path, cwd: Path) -> None:
    path.write_text(
        dedent(
            f"""
            controller:
              host: "127.0.0.1"
              port: 9000
              allowed_path_roots: ["{cwd}"]
            services:
              - id: dummy
                name: Dummy
                cwd: "{cwd}"
                entry_file: server.py
                command: ["/usr/bin/python3", "server.py"]
                lifecycle:
                  mode: manual
                  autostart: false
                  stop_visibility: primary
                  restart_visibility: primary
                  unmanaged_policy: manage
                actions:
                  - id: start
                    label: Start
                    type: process_start
                    enabled: true
                  - id: stop
                    label: Stop
                    type: process_stop
                    enabled: true
                    strategy:
                      signal: SIGINT
                      timeout_seconds: 3
                      confirm_required: false
                      fallback: [SIGTERM, SIGKILL]
                  - id: restart
                    label: Restart
                    type: process_restart
                    enabled: true
            actions: []
            """
        ).strip(),
        encoding="utf-8",
    )


def _login(client) -> str:
    assert client.post("/api/auth/login", json={"password": "secret"}).status_code == 200
    return client.get("/api/auth/me").get_json()["csrf_token"]


def test_pm2_backend_preserves_service_api_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(PASSWORD_ENV, "secret")
    config = tmp_path / "config.yml"
    _config(config, tmp_path)
    fake = FakePm2()
    app = backend_app.create_app(
        config_path=config,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "dist",
        process_backend="pm2",
        pm2_runner=fake,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    csrf = _login(client)

    before = client.get("/api/services").get_json()["services"][0]["runtime"]
    assert before["alive"] is False
    assert before["state"] == "unknown"
    assert [command[0] for command in fake.commands].count("jlist") == 1

    started = client.post(
        "/api/services/dummy/actions/start",
        headers={"X-CSRF-Token": csrf},
    )
    assert started.status_code == 200
    runtime = started.get_json()["service"]["runtime"]
    assert runtime["alive"] is True
    assert runtime["state"] == "running"
    assert runtime["pid"] == os.getpid()
    assert "resource" in runtime

    stopped = client.post(
        "/api/services/dummy/actions/stop",
        headers={"X-CSRF-Token": csrf},
    )
    assert stopped.status_code == 200
    runtime = stopped.get_json()["service"]["runtime"]
    assert runtime["alive"] is False
    assert runtime["state"] == "stopped"
    assert runtime["pid"] is None

    command_names = [command[0] for command in fake.commands]
    assert "start" in command_names
    assert "stop" in command_names
    assert command_names.count("jlist") >= 3
    assert app.config["process_backend"] == "pm2"


def test_pm2_list_failure_is_structured_and_redacted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(PASSWORD_ENV, "secret")
    config = tmp_path / "config.yml"
    _config(config, tmp_path)

    def failed_runner(*_args):
        return CommandResult(1, "", "CONTROL_PASSWORD=do-not-leak")

    app = backend_app.create_app(
        config_path=config,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "dist",
        process_backend="pm2",
        pm2_runner=failed_runner,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    _login(client)

    response = client.get("/api/services")
    assert response.status_code == 503
    assert response.get_json()["error"] == "pm2_unavailable"
    assert "do-not-leak" not in response.get_data(as_text=True)
