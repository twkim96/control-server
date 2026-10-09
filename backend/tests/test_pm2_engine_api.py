"""PM2 engine API contracts without running Node, npm, or a PM2 daemon."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

import app as backend_app
import pm2_engine
from auth import PASSWORD_ENV
from pm2_manager import CommandResult


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv(PASSWORD_ENV, "secret")
    node = tmp_path / "node"
    node.touch()
    monkeypatch.setenv("CONTROL_PM2_NODE", str(node))
    monkeypatch.setenv("CONTROL_PM2_CLI", str(tmp_path / "missing/bin/pm2"))
    config = tmp_path / "config.yml"
    config.write_text('controller:\n  host: "127.0.0.1"\n  port: 9000\n  allowed_path_roots: ["/tmp"]\nservices: []\nactions: []\n')
    app = backend_app.create_app(
        config_path=config, runtime_dir=tmp_path / "runtime",
        log_dir=tmp_path / "logs", frontend_dist=tmp_path / "dist",
        process_backend="pm2", pm2_runner=lambda *_: CommandResult(0, "[]", ""),
    )
    app.config["TESTING"] = True
    engine = app.config["pm2_engine"]
    engine.app_root = tmp_path / "app"
    script = engine.app_root / "lib/pm2-engine.mjs"
    script.parent.mkdir(parents=True)
    script.touch()
    package_root = engine.root / "current/node_modules/pm2"
    (package_root / "bin").mkdir(parents=True)
    (package_root / "bin/pm2").touch()
    _write_json(package_root / "package.json", {"version": "6.0.0"})
    _write_json(engine.root / "latest.json", {
        "version": "6.0.1", "checked_at": "2026-09-06T00:00:00Z",
    })
    run = Mock(side_effect=AssertionError("Unexpected subprocess/network check"))
    spawn = Mock(side_effect=AssertionError("Unexpected updater launch"))
    monkeypatch.setattr(pm2_engine.subprocess, "run", run)
    monkeypatch.setattr(pm2_engine.subprocess, "Popen", spawn)
    processes = {}

    def process(pid):
        if pid not in processes:
            raise psutil.NoSuchProcess(pid)
        return processes[pid]

    monkeypatch.setattr(pm2_engine, "psutil", SimpleNamespace(
        Process=process, Error=psutil.Error, STATUS_ZOMBIE=psutil.STATUS_ZOMBIE,
    ))
    return SimpleNamespace(app=app, client=app.test_client(), engine=engine,
                           run=run, spawn=spawn, processes=processes)


def _login(harness):
    assert harness.client.post("/api/auth/login", json={"password": "secret"}).status_code == 200
    return {"X-CSRF-Token": harness.client.get("/api/auth/me").get_json()["csrf_token"]}


@pytest.mark.parametrize("method,path", [
    ("get", "/api/system/pm2"),
    ("post", "/api/system/pm2/check"),
    ("post", "/api/system/pm2/update"),
])
def test_engine_endpoints_require_authentication(harness, method, path):
    assert getattr(harness.client, method)(path).status_code == 401
    harness.run.assert_not_called()
    harness.spawn.assert_not_called()


@pytest.mark.parametrize("path", ["/api/system/pm2/check", "/api/system/pm2/update"])
def test_engine_mutations_require_valid_csrf(harness, path):
    _login(harness)
    assert harness.client.post(path).status_code == 403
    assert harness.client.post(path, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    harness.run.assert_not_called()
    harness.spawn.assert_not_called()


def test_status_reads_cached_versions_and_matching_daemon_without_commands(harness):
    _login(harness)
    engine = harness.engine
    pid_file = engine.runtime / "pm2/pm2.pid"
    pid_file.parent.mkdir()
    pid_file.write_text("12345")
    daemon = Mock()
    daemon.cmdline.return_value = [f"PM2 v5.9.0: God Daemon ({engine.runtime / 'pm2'})"]
    harness.processes[12345] = daemon
    result = harness.client.get("/api/system/pm2")
    assert result.status_code == 200
    body = result.get_json()
    assert body["supported"] is True
    assert body["current_version"] == "6.0.0"
    assert body["daemon_version"] == "5.9.0"
    assert body["latest_version"] == "6.0.1"
    assert body["checked_at"] == "2026-09-06T00:00:00Z"
    assert body["update_available"] is True
    # Equal installed/latest versions still permit repairing a stale daemon.
    _write_json(engine.root / "latest.json", {"version": "6.0.0"})
    assert harness.client.get("/api/system/pm2").get_json()["update_available"] is True
    daemon.cmdline.return_value = ["PM2 v5.9.0: God Daemon (/another/runtime)"]
    body = harness.client.get("/api/system/pm2").get_json()
    assert body["daemon_version"] is None
    assert body["update_available"] is False
    harness.run.assert_not_called()
    harness.spawn.assert_not_called()


def test_check_refreshes_cached_version_with_authorized_post(harness):
    headers = _login(harness)

    def check(argv, **kwargs):
        assert argv[2] == "check"
        assert kwargs["timeout"] == 12
        _write_json(harness.engine.root / "latest.json", {
            "version": "6.1.0", "checked_at": "2026-09-06T01:00:00Z",
        })
        return SimpleNamespace(returncode=0)

    harness.run.side_effect = check
    response = harness.client.post("/api/system/pm2/check", headers=headers)
    assert response.status_code == 200
    assert response.get_json()["latest_version"] == "6.1.0"
    assert response.get_json()["checked_at"] == "2026-09-06T01:00:00Z"
    harness.run.assert_called_once()
    harness.spawn.assert_not_called()


def test_update_starts_detached_job_and_rejects_duplicate(harness):
    headers = _login(harness)
    child = Mock(pid=23456)
    child.poll.return_value = None
    harness.spawn.side_effect = None
    harness.spawn.return_value = child
    response = harness.client.post("/api/system/pm2/update", headers=headers)
    assert response.status_code == 202
    job = response.get_json()["job"]
    assert job["status"] == "running"
    assert job["id"]
    assert job["started_at"]
    assert "pid" not in job
    argv = harness.spawn.call_args.args[0]
    assert argv[2] == "update"
    assert argv[-1] == job["id"]
    assert harness.spawn.call_args.kwargs["start_new_session"] is True
    assert harness.client.post("/api/system/pm2/update", headers=headers).status_code == 409
    harness.spawn.assert_called_once()
    # Simulate the detached updater publishing progress, then a new controller instance.
    _write_json(harness.engine.root / "job.json", {
        **job, "pid": child.pid, "phase": "installing", "message": "설치 중",
    })
    process = Mock()
    process.is_running.return_value = True
    process.status.return_value = psutil.STATUS_RUNNING
    process.cmdline.return_value = ["node", str(harness.engine.app_root / "lib/pm2-engine.mjs")]
    harness.processes[child.pid] = process
    harness.engine._child = None
    saved = harness.client.get("/api/system/pm2").get_json()["job"]
    assert saved["id"] == job["id"]
    assert saved["phase"] == "installing"
    assert saved["status"] == "running"
    assert "pid" not in saved
    assert harness.client.post("/api/system/pm2/update", headers=headers).status_code == 409
    harness.spawn.assert_called_once()


@pytest.mark.parametrize("process_state", ["missing", "zombie", "unrelated"])
def test_stale_job_is_interrupted_and_recovery_reenables_update(harness, process_state):
    _login(harness)
    _write_json(harness.engine.root / "latest.json", {"version": "6.0.0"})
    _write_json(harness.engine.root / "job.json", {
        "id": "interrupted", "pid": 34567, "status": "running",
        "phase": "installing", "message": "설치 중", "started_at": "2026-09-06T00:00:00Z",
    })
    if process_state != "missing":
        process = Mock()
        process.is_running.return_value = True
        process.status.return_value = psutil.STATUS_ZOMBIE if process_state == "zombie" else psutil.STATUS_RUNNING
        process.cmdline.return_value = ["unrelated-program"]
        harness.processes[34567] = process
    body = harness.client.get("/api/system/pm2").get_json()
    assert body["job"]["status"] == "failed"
    assert body["job"]["phase"] == "complete"
    assert "중단" in body["job"]["message"]
    assert "pid" not in body["job"]
    assert body["update_available"] is False
    _write_json(harness.engine.root / "recovery.json", {"from_version": "6.0.0"})
    assert harness.client.get("/api/system/pm2").get_json()["update_available"] is True
    harness.run.assert_not_called()
    harness.spawn.assert_not_called()


def test_native_backend_reports_unsupported_without_launching(harness):
    native = backend_app.create_app(
        config_path=harness.engine.config_path,
        runtime_dir=harness.engine.runtime / "native",
        log_dir=harness.engine.runtime / "native-logs",
        frontend_dist=harness.engine.runtime / "no-dist", process_backend="native",
    )
    harness.client = native.test_client()
    headers = _login(harness)
    response = harness.client.get("/api/system/pm2")
    assert response.status_code == 200
    assert response.get_json()["supported"] is False
    assert response.get_json()["reason"]
    assert harness.client.post("/api/system/pm2/check", headers=headers).status_code == 503
    assert harness.client.post("/api/system/pm2/update", headers=headers).status_code == 409
    harness.run.assert_not_called()
    harness.spawn.assert_not_called()


def test_updater_exit_before_first_status_write_remains_visible_until_next_job(harness):
    headers = _login(harness)
    _write_json(harness.engine.root / "job.json", {
        "id": "older-success", "status": "succeeded", "phase": "complete",
    })
    child = Mock(pid=24680)
    child.poll.return_value = None
    harness.spawn.side_effect = None
    harness.spawn.return_value = child
    submitted = harness.client.post("/api/system/pm2/update", headers=headers).get_json()["job"]
    child.poll.return_value = 1
    for _ in range(2):
        job = harness.client.get("/api/system/pm2").get_json()["job"]
        assert job["id"] == submitted["id"]
        assert job["status"] == "failed"
        assert "중단" in job["message"]
    _write_json(harness.engine.root / "job.json", {
        "id": "later-cli-job", "status": "succeeded", "phase": "complete",
    })
    assert harness.client.get("/api/system/pm2").get_json()["job"]["id"] == "later-cli-job"


def test_immediate_status_keeps_pending_job_before_updater_writes(harness):
    headers = _login(harness)
    child = Mock(pid=45678)
    child.poll.return_value = None
    harness.spawn.side_effect = None
    harness.spawn.return_value = child
    started = harness.client.post("/api/system/pm2/update", headers=headers)
    assert started.status_code == 202
    assert not (harness.engine.root / "job.json").exists()
    # psutil has no entry yet: the owned live child must bridge process startup.
    assert child.pid not in harness.processes
    response = harness.client.get("/api/system/pm2")
    assert response.status_code == 200
    assert response.get_json()["job"] == started.get_json()["job"]
    assert response.get_json()["job"]["status"] == "running"
    assert "pid" not in response.get_json()["job"]
    harness.spawn.assert_called_once()
    harness.run.assert_not_called()


def test_persisted_cli_update_job_stays_running(harness):
    headers = _login(harness)
    _write_json(harness.engine.root / "job.json", {
        "id": "cli-update", "pid": 56789, "status": "running",
        "phase": "installing", "message": "설치 중", "started_at": "2026-09-06T00:00:00Z",
    })
    process = Mock()
    process.is_running.return_value = True
    process.status.return_value = psutil.STATUS_RUNNING
    process.cmdline.return_value = [
        "node", str(harness.engine.app_root / "bin/control-server.mjs"), "pm2-update",
    ]
    harness.processes[56789] = process
    assert harness.engine._child is None
    response = harness.client.get("/api/system/pm2")
    assert response.status_code == 200
    job = response.get_json()["job"]
    assert job["id"] == "cli-update"
    assert job["status"] == "running"
    assert job["phase"] == "installing"
    assert "pid" not in job
    assert harness.client.post("/api/system/pm2/update", headers=headers).status_code == 409
    harness.spawn.assert_not_called()
    harness.run.assert_not_called()
