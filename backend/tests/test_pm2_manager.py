from __future__ import annotations

import json
import os
import signal
import threading
import time
from dataclasses import replace
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
from process_manager import AdoptDiagnostics, AdoptEvaluation, RuntimeState


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
    # PM2 owns the final SIGKILL deadline. Reserve one timeout window for
    # SIGINT and one for the configured graceful SIGTERM fallback.
    assert app["kill_timeout"] == 15000
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


def test_manifest_rejects_primary_signal_pm2_cannot_honor(tmp_path: Path) -> None:
    manager = _manager(tmp_path, lambda *_: CommandResult(0, "[]", ""))
    service = _service(tmp_path)
    stop = replace(
        service.actions[0],
        stop_strategy=StopStrategy("SIGTERM", 1.0, False, ("SIGKILL",)),
    )

    with pytest.raises(Pm2Error, match="SIGINT만 지원"):
        manager.build_manifest(replace(service, actions=(stop,)))


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


def test_reconcile_rejects_removing_online_pm2_service(tmp_path: Path) -> None:
    service = _service(tmp_path, sid="removed")
    payload = [
        {
            "name": "server-control--removed",
            "pid": 88,
            "pm2_env": {"status": "online"},
        }
    ]
    manager = _manager(
        tmp_path,
        lambda *_: CommandResult(0, json.dumps(payload, separators=(",", ":")), ""),
    )
    manager.activate_service(service)

    with pytest.raises(Pm2Error, match="config reload"):
        manager.reconcile_service_definitions(())

    assert manager._active_services[service.id] == service
    assert service.id not in manager._retired_services


def test_reconcile_cleans_stopped_entry_and_blocks_stale_start(tmp_path: Path) -> None:
    service = _service(tmp_path, sid="removed")
    commands: list[list[str]] = []
    payload = [
        {
            "name": "server-control--removed",
            "pid": 0,
            "pm2_env": {"status": "stopped"},
        }
    ]

    def runner(argv, _env, _timeout):
        commands.append(argv[1:])
        if argv[1] == "jlist":
            return CommandResult(0, json.dumps(payload, separators=(",", ":")), "")
        return CommandResult(0, "ok", "")

    manager = _manager(tmp_path, runner)
    manager.activate_service(service)
    manifest = manager.write_manifest(service)

    manager.reconcile_service_definitions(())

    assert ["delete", "server-control--removed"] in commands
    assert not manifest.exists()
    assert service.id in manager._retired_services
    with pytest.raises(Pm2Error, match="삭제된 서비스"):
        manager.start(service)


def test_reconcile_blocks_stale_definition_after_reload(tmp_path: Path) -> None:
    manager = _manager(tmp_path, lambda *_: CommandResult(0, "[]", ""))
    old = _service(tmp_path, sid="reloadable")
    new = replace(old, name="New definition")
    manager.activate_service(old)

    manager.reconcile_service_definitions((new,))

    with pytest.raises(Pm2Error, match="최신 설정"):
        manager.start(old)


def test_reconcile_removal_serializes_with_concurrent_start(tmp_path: Path) -> None:
    service = _service(tmp_path, sid="removed")
    first_snapshot_done = threading.Event()
    release_reconcile = threading.Event()
    jlist_calls = 0

    def runner(argv, _env, _timeout):
        nonlocal jlist_calls
        if argv[1] == "jlist":
            jlist_calls += 1
            if jlist_calls == 1:
                first_snapshot_done.set()
                assert release_reconcile.wait(timeout=5)
            return CommandResult(0, "[]", "")
        raise AssertionError(f"unexpected PM2 command: {argv[1]}")

    manager = _manager(tmp_path, runner)
    manager.activate_service(service)
    reload_errors: list[Exception] = []
    start_errors: list[Exception] = []
    reload_thread = threading.Thread(
        target=lambda: _capture_error(
            reload_errors,
            lambda: manager.reconcile_service_definitions(()),
        )
    )
    start_thread = threading.Thread(
        target=lambda: _capture_error(start_errors, lambda: manager.start(service))
    )

    reload_thread.start()
    assert first_snapshot_done.wait(timeout=2)
    start_thread.start()
    time.sleep(0.05)
    assert start_thread.is_alive()
    release_reconcile.set()
    reload_thread.join(timeout=2)
    start_thread.join(timeout=2)

    assert reload_errors == []
    assert len(start_errors) == 1
    assert "삭제된 서비스" in str(start_errors[0])


def test_stop_rotates_log_with_service_policy(tmp_path: Path) -> None:
    service = _service(tmp_path)
    online = True

    def runner(argv, _env, _timeout):
        nonlocal online
        if argv[1] == "stop":
            online = False
            return CommandResult(0, "ok", "")
        payload = [
            {
                "name": "server-control--example",
                "pid": 88 if online else 0,
                "pm2_env": {
                    "status": "online" if online else "stopped",
                    "kill_timeout": 7500,
                },
            }
        ]
        return CommandResult(0, json.dumps(payload, separators=(",", ":")), "")

    manager = _manager(tmp_path, runner)
    manager.activate_service(service)
    log_path = tmp_path / "logs" / "example.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_bytes(b"x" * 2048)

    manager.stop(service, service.actions[0].stop_strategy)

    assert not log_path.exists()
    assert (tmp_path / "logs" / "example.log.1").stat().st_size == 2048


def test_stop_schedules_graceful_fallback_before_pm2_kill_deadline(tmp_path: Path) -> None:
    service = _service(tmp_path)
    online = True
    stop_timeout = 0.0

    def runner(argv, _env, timeout):
        nonlocal online, stop_timeout
        if argv[1] == "stop":
            stop_timeout = timeout
            online = False
            return CommandResult(0, "ok", "")
        payload = [
            {
                "name": "server-control--example",
                "pid": 88 if online else 0,
                "pm2_env": {
                    "status": "online" if online else "stopped",
                    "kill_timeout": 7500,
                },
            }
        ]
        return CommandResult(0, json.dumps(payload, separators=(",", ":")), "")

    manager = _manager(tmp_path, runner)
    manager.activate_service(service)
    with (
        patch("pm2_manager._process_tree_identities", return_value=((88, 1.0),)),
        patch("pm2_manager._send_fallback_signals") as send_fallbacks,
    ):
        manager.stop(service, service.actions[0].stop_strategy)

    send_fallbacks.assert_called_once()
    assert send_fallbacks.call_args.args[1] == ("SIGTERM",)
    assert send_fallbacks.call_args.args[2] < service.actions[0].stop_strategy.timeout_seconds
    assert stop_timeout >= 20.0


def test_fallback_reaches_child_after_parent_has_exited() -> None:
    from pm2_manager import _send_fallback_signals

    child = Mock()
    child.create_time.return_value = 2.0
    child.is_running.return_value = True
    child.status.return_value = psutil.STATUS_SLEEPING

    def process_for(pid):
        if pid == 88:
            raise psutil.NoSuchProcess(pid)
        return child

    with (
        patch("pm2_manager.psutil.Process", side_effect=process_for),
        patch("pm2_manager.os.kill") as kill,
    ):
        _send_fallback_signals(
            ((88, 1.0), (99, 2.0)),
            ("SIGTERM",),
            0.0,
            threading.Event(),
        )

    kill.assert_called_once_with(99, int(signal.SIGTERM))


@pytest.mark.parametrize(
    ("ok", "reason", "expected_reason"),
    [
        (False, "cmdline_mismatch", "cmdline_mismatch"),
        (False, "cwd_mismatch", "cwd_mismatch"),
        (False, "health_failed", "health_failed"),
        (True, "ok", "pm2_exclusive"),
    ],
)
def test_pm2_adopt_diagnostics_preserve_safety_failures(
    tmp_path: Path,
    ok: bool,
    reason: str,
    expected_reason: str,
) -> None:
    evaluation = AdoptEvaluation(
        diagnostics=AdoptDiagnostics(ok=ok, reason=reason, candidate_pid=88),
        candidate=None,
        state_snapshot=RuntimeState(),
    )
    external_helper = Mock()
    external_helper.evaluate_adopt.return_value = evaluation
    manager = Pm2Manager(
        tmp_path / "runtime",
        tmp_path / "logs",
        runner=lambda *_: CommandResult(0, "[]", ""),
        external_helper=external_helper,
    )

    result = manager.evaluate_adopt(_service(tmp_path))

    assert result.diagnostics.reason == expected_reason
    assert result.diagnostics.ok is False


def _capture_error(errors: list[Exception], operation) -> None:
    try:
        operation()
    except Exception as exc:  # noqa: BLE001 - thread result is asserted by the test
        errors.append(exc)


@pytest.mark.parametrize("service_id", ["bad/id", " space", "", "한글"])
def test_app_name_rejects_unsafe_ids(service_id: str) -> None:
    with pytest.raises(Pm2Error):
        Pm2Manager.app_name(service_id)


def test_rejects_pm2_home_that_exceeds_macos_socket_limit(tmp_path: Path) -> None:
    long_runtime = tmp_path / ("x" * 110)
    with pytest.raises(Pm2Error, match="too long"):
        Pm2Manager(long_runtime, tmp_path / "logs")
