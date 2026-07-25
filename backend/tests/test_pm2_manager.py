from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import psutil

from config_schema import (
    ActionConfig,
    HealthConfig,
    HttpsConfig,
    LifecycleConfig,
    LogConfig,
    ServiceConfig,
    StopStrategy,
)
from pm2_manager import CommandResult, Pm2Error, Pm2Manager


def _service(tmp_path: Path, *, sid: str = "example") -> ServiceConfig:
    return ServiceConfig(
        id=sid,
        name="Example",
        description="",
        cwd=str(tmp_path),
        entry_file="server.py",
        command=("/usr/bin/python3", "-u", "server.py", "--name", "hello world"),
        env={"EXAMPLE": "yes"},
        port=12345,
        port_env_name="PORT",
        open_url=None,
        health=HealthConfig(False, "http", None, 2.0, True),
        log=LogConfig(True, 200, 1024, 2),
        lifecycle=LifecycleConfig("manual", False, "primary", "primary", "manage"),
        actions=(
            ActionConfig(
                "stop",
                "Stop",
                "process_stop",
                True,
                StopStrategy("SIGINT", 7.5, False, ("SIGTERM", "SIGKILL")),
            ),
        ),
        https=HttpsConfig(False, None, None, "HTTPS", "SSL_CERT_FILE", "SSL_KEY_FILE"),
    )


def _manager(tmp_path: Path, runner, *, ttl: float = 2.0, **kwargs) -> Pm2Manager:
    return Pm2Manager(
        tmp_path / "runtime",
        tmp_path / "logs",
        wrapper=tmp_path / "pm2ctl.sh",
        runner=runner,
        snapshot_ttl_seconds=ttl,
        **kwargs,
    )


def test_manifest_preserves_command_args_env_and_private_mode(tmp_path: Path) -> None:
    manager = _manager(tmp_path, lambda *_: CommandResult(0, "[]", ""))
    service = _service(tmp_path)

    path = manager.write_manifest(service)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    app = manifest["apps"][0]

    assert app["name"] == "server-control--example"
    assert app["script"] == "/usr/bin/python3"
    assert app["args"] == ["-u", "server.py", "--name", "hello world"]
    assert app["interpreter"] == "none"
    assert app["cwd"] == str(tmp_path)
    assert app["env"] == {"EXAMPLE": "yes", "PORT": "12345", "PYTHONUNBUFFERED": "1"}
    assert app["kill_timeout"] == 7500
    assert app["log_file"] == str(tmp_path / "logs" / "example.log")
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700


def test_manifest_resolves_relative_https_paths(tmp_path: Path) -> None:
    manager = _manager(tmp_path, lambda *_: CommandResult(0, "[]", ""))
    service = _service(tmp_path)
    service = ServiceConfig(
        **{
            **service.__dict__,
            "https": HttpsConfig(True, "certs/a.crt", "certs/a.key", "HTTPS", "CERT", "KEY"),
        }
    )
    app = manager.build_manifest(service)["apps"][0]
    assert app["env"]["HTTPS"] == "1"
    assert app["env"]["CERT"] == str(tmp_path / "certs/a.crt")
    assert app["env"]["KEY"] == str(tmp_path / "certs/a.key")


def test_snapshot_ignores_banner_foreign_apps_and_env(tmp_path: Path) -> None:
    payload = [
        {
            "name": "server-control--alpha",
            "pid": 321,
            "pm2_env": {
                "status": "online",
                "pm_uptime": 1_700_000_000_000,
                "restart_time": 3,
                "exit_code": 0,
                "SECRET": "must-not-be-returned",
            },
        },
        {"name": "unrelated", "pid": 999, "pm2_env": {"status": "online"}},
    ]
    output = "[PM2] daemon started\n" + json.dumps(payload, separators=(",", ":")) + "\n"
    manager = _manager(tmp_path, lambda *_: CommandResult(0, output, ""))

    snapshot = manager.snapshot()
    assert list(snapshot) == ["alpha"]
    assert snapshot["alpha"].pid == 321
    assert snapshot["alpha"].started_at == 1_700_000_000.0
    assert snapshot["alpha"].restart_count == 3
    assert "SECRET" not in repr(snapshot["alpha"])


def test_snapshot_ttl_and_singleflight_use_one_cli_call(tmp_path: Path) -> None:
    calls = 0
    lock = threading.Lock()
    entered = threading.Event()
    release = threading.Event()

    def runner(*_args):
        nonlocal calls
        with lock:
            calls += 1
        entered.set()
        assert release.wait(2)
        return CommandResult(0, "[]", "")

    manager = _manager(tmp_path, runner, ttl=10.0)
    results: list[dict] = []
    threads = [
        threading.Thread(target=lambda: results.append(manager.snapshot(nonblocking=True)))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    assert entered.wait(1)
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert calls == 1
    assert len(results) == 8
    assert manager.snapshot() == {}
    assert calls == 1


def test_expired_read_returns_last_good_while_one_background_refresh_runs(
    tmp_path: Path,
) -> None:
    calls = 0
    refresh_entered = threading.Event()
    release_refresh = threading.Event()
    payload = [
        {
            "name": "server-control--example",
            "pid": 88,
            "pm2_env": {"status": "online"},
        }
    ]

    def runner(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return CommandResult(0, json.dumps(payload, separators=(",", ":")), "")
        refresh_entered.set()
        assert release_refresh.wait(2)
        return CommandResult(1, "", "CONTROL_PASSWORD=must-not-leak")

    manager = _manager(
        tmp_path,
        runner,
        ttl=0.01,
        snapshot_retry_seconds=0.1,
        slow_refresh_seconds=0.01,
    )
    assert manager.snapshot()["example"].pid == 88
    time.sleep(0.11)

    started = time.monotonic()
    results: list[dict] = []
    threads = [
        threading.Thread(target=lambda: results.append(manager.snapshot(nonblocking=True)))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert time.monotonic() - started < 0.5
    assert refresh_entered.wait(1)
    assert len(results) == 8
    assert all(result["example"].pid == 88 for result in results)
    assert calls == 2
    time.sleep(0.11)
    assert manager.snapshot_diagnostics()["degraded"] is True

    release_refresh.set()
    for _ in range(100):
        diagnostics = manager.snapshot_diagnostics()
        if not diagnostics["refreshing"]:
            break
        time.sleep(0.01)
    assert diagnostics["degraded"] is True
    assert diagnostics["last_error"] == "PM2 command failed: jlist"
    assert "must-not-leak" not in str(diagnostics)


def test_forced_snapshot_never_falls_back_to_last_good(tmp_path: Path) -> None:
    calls = 0
    payload = [
        {
            "name": "server-control--example",
            "pid": 88,
            "pm2_env": {"status": "online"},
        }
    ]

    def runner(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return CommandResult(0, json.dumps(payload, separators=(",", ":")), "")
        return CommandResult(1, "", "private failure")

    manager = _manager(tmp_path, runner)
    assert manager.snapshot()["example"].pid == 88
    manager.invalidate()

    with pytest.raises(Pm2Error, match="PM2 command failed: jlist"):
        manager.snapshot(force=True)
    assert calls == 2


def test_mutation_invalidates_snapshot_and_uses_namespaced_name(tmp_path: Path) -> None:
    service = _service(tmp_path)
    commands: list[list[str]] = []
    online = False

    def runner(argv, _env, _timeout):
        nonlocal online
        commands.append(argv[1:])
        if argv[1] == "start":
            online = True
            return CommandResult(0, "ok", "")
        payload = []
        if online:
            payload = [{"name": "server-control--example", "pid": 88, "pm2_env": {"status": "online"}}]
        return CommandResult(0, json.dumps(payload, separators=(",", ":")), "")

    manager = _manager(tmp_path, runner)
    state = manager.start(service)

    assert state.pid == 88
    assert commands[0] == ["jlist"]
    assert commands[1] == ["start", str(tmp_path / "runtime/pm2/manifests/example.json"), "--only", "server-control--example"]
    assert commands[2] == ["jlist"]


def test_start_reconciles_online_process_after_cli_failure(tmp_path: Path) -> None:
    service = _service(tmp_path)
    started = False

    def runner(argv, _env, _timeout):
        nonlocal started
        if argv[1] == "start":
            started = True
            return CommandResult(1, "", "private PM2 failure detail")
        payload = []
        if started:
            payload = [
                {
                    "name": "server-control--example",
                    "pid": 88,
                    "pm2_env": {"status": "online"},
                }
            ]
        return CommandResult(0, json.dumps(payload, separators=(",", ":")), "")

    manager = _manager(tmp_path, runner)
    state = manager.start(service)

    assert state.pid == 88


def test_start_retries_transient_post_start_state_failure(tmp_path: Path) -> None:
    service = _service(tmp_path)
    jlist_calls = 0

    def runner(argv, _env, _timeout):
        nonlocal jlist_calls
        if argv[1] == "start":
            return CommandResult(0, "ok", "")
        jlist_calls += 1
        if jlist_calls == 1:
            return CommandResult(0, "[]", "")
        if jlist_calls == 2:
            return CommandResult(1, "", "transient")
        payload = [
            {
                "name": "server-control--example",
                "pid": 88,
                "pm2_env": {"status": "online"},
            }
        ]
        return CommandResult(0, json.dumps(payload, separators=(",", ":")), "")

    manager = _manager(tmp_path, runner)
    state = manager.start(service)

    assert state.pid == 88
    assert jlist_calls == 3


def test_command_failure_does_not_expose_stderr(tmp_path: Path) -> None:
    def runner(*_args):
        return CommandResult(1, "", "CONTROL_PASSWORD=secret")

    manager = _manager(tmp_path, runner)
    with pytest.raises(Pm2Error, match="PM2 command failed: jlist") as exc:
        manager.snapshot()
    assert "secret" not in str(exc.value)


def test_start_rejects_existing_pm2_process_before_manifest(tmp_path: Path) -> None:
    payload = [{"name": "server-control--example", "pid": 99, "pm2_env": {"status": "online"}}]
    calls: list[str] = []

    def runner(argv, _env, _timeout):
        calls.append(argv[1])
        return CommandResult(0, json.dumps(payload, separators=(",", ":")), "")

    manager = _manager(tmp_path, runner)
    with pytest.raises(Pm2Error, match="already running"):
        manager.start(_service(tmp_path))
    assert calls == ["jlist"]


def test_online_pm2_pid_stays_alive_on_transient_psutil_denial(tmp_path: Path) -> None:
    payload = [{"name": "server-control--example", "pid": 99999, "pm2_env": {"status": "online"}}]
    manager = _manager(
        tmp_path,
        lambda *_: CommandResult(0, json.dumps(payload, separators=(",", ":")), ""),
    )
    with patch("pm2_manager.psutil.Process", side_effect=psutil.AccessDenied(99999)):
        state, alive = manager.inspect_state("example")
    assert alive is True
    assert state.pid == 99999
    assert state.create_time is None


def test_external_kill_requires_confirmed_absence_from_pm2(tmp_path: Path) -> None:
    payload = [
        {
            "name": "server-control--example",
            "pid": 88,
            "pm2_env": {"status": "online"},
        }
    ]
    external_helper = Mock()
    manager = Pm2Manager(
        tmp_path / "runtime",
        tmp_path / "logs",
        wrapper=tmp_path / "pm2ctl.sh",
        runner=lambda *_: CommandResult(0, json.dumps(payload, separators=(",", ":")), ""),
        external_helper=external_helper,
    )

    with pytest.raises(Pm2Error, match="external kill refused"):
        manager.kill_external(_service(tmp_path), expected_pid=88)
    external_helper.kill_external.assert_not_called()


@pytest.mark.parametrize("service_id", ["bad/id", " space", "", "한글"])
def test_app_name_rejects_unsafe_ids(service_id: str) -> None:
    with pytest.raises(Pm2Error):
        Pm2Manager.app_name(service_id)


def test_rejects_pm2_home_that_exceeds_macos_socket_limit(tmp_path: Path) -> None:
    long_runtime = tmp_path / ("x" * 110)
    with pytest.raises(Pm2Error, match="too long"):
        Pm2Manager(long_runtime, tmp_path / "logs")
