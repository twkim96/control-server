from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from textwrap import dedent

import app as backend_app
from auth import PASSWORD_ENV
from pm2_manager import CommandResult


class FakePm2:
    def __init__(self) -> None:
        self.status: str | None = None
        self.commands: list[list[str]] = []
        self.fail_jlist = False

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
            if self.fail_jlist:
                return CommandResult(1, "", "CONTROL_PASSWORD=do-not-leak")
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


def _config(path: Path, cwd: Path, *, autostart: bool = False) -> None:
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
                  autostart: {str(autostart).lower()}
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


def test_pm2_cold_list_failure_is_degraded_and_redacted(tmp_path: Path, monkeypatch) -> None:
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
    assert response.status_code == 200
    for _ in range(100):
        diagnostics = app.config["process_manager"].snapshot_diagnostics()
        if diagnostics["degraded"]:
            break
        time.sleep(0.01)
    response = client.get("/api/services")
    assert response.status_code == 200
    assert response.get_json()["supervisor"]["degraded"] is True
    assert response.get_json()["supervisor"]["last_error"] == "PM2 command failed: jlist"
    assert "do-not-leak" not in response.get_data(as_text=True)


def test_pm2_list_uses_last_good_snapshot_after_transient_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
    _login(client)

    initial = client.get("/api/services")
    assert initial.status_code == 200
    manager = app.config["process_manager"]
    manager.invalidate()
    fake.fail_jlist = True

    stale = client.get("/api/services")
    assert stale.status_code == 200
    initial_runtime = initial.get_json()["services"][0]["runtime"]
    stale_runtime = stale.get_json()["services"][0]["runtime"]
    assert stale_runtime["state"] == initial_runtime["state"]
    assert stale_runtime["alive"] == initial_runtime["alive"]
    assert stale_runtime["pid"] == initial_runtime["pid"]

    for _ in range(100):
        diagnostics = manager.snapshot_diagnostics()
        if diagnostics["degraded"]:
            break
        time.sleep(0.01)
    response = client.get("/api/services")
    body = response.get_json()
    assert response.status_code == 200
    assert body["supervisor"]["degraded"] is True
    assert body["supervisor"]["last_error"] == "PM2 command failed: jlist"
    assert "do-not-leak" not in response.get_data(as_text=True)


def test_pm2_slow_cold_start_does_not_delay_control_server_or_list_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(PASSWORD_ENV, "secret")
    config = tmp_path / "config.yml"
    _config(config, tmp_path, autostart=True)
    fake = FakePm2()
    entered = threading.Event()
    release = threading.Event()
    first_jlist = True

    def blocking_runner(argv, env, timeout):
        nonlocal first_jlist
        if argv[1] == "jlist" and first_jlist:
            first_jlist = False
            entered.set()
            assert release.wait(2)
        return fake(argv, env, timeout)

    started = time.monotonic()
    app = backend_app.create_app(
        config_path=config,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "dist",
        process_backend="pm2",
        pm2_runner=blocking_runner,
        run_autostart=True,
    )
    assert time.monotonic() - started < 0.5
    assert entered.wait(1)
    app.config["TESTING"] = True
    client = app.test_client()
    _login(client)

    list_started = time.monotonic()
    response = client.get("/api/services")
    assert time.monotonic() - list_started < 0.5
    assert response.status_code == 200
    assert response.get_json()["supervisor"]["refreshing"] is True

    release.set()
    lifecycle_thread = app.config["startup_lifecycle_thread"]
    lifecycle_thread.join(timeout=2)
    assert not lifecycle_thread.is_alive()
    assert fake.status == "online"
