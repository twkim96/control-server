from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
import os
import threading
from types import SimpleNamespace

import pytest

from config_schema import ActionConfig, HealthConfig, LifecycleConfig, LogConfig, RecoveryConfig, ServiceConfig
from pm2_manager import Pm2Process
from process_manager import RuntimeState
from service_operations import OperationsError, ServiceOperations


def service(sid="one", *, deps=(), group="apps", recovery=None, autostart=False):
    return ServiceConfig(id=sid, name=sid, description="", cwd="/tmp", entry_file="s.py",
        command=("python", "s.py"), env={}, port=None, port_env_name=None, open_url=None,
        health=HealthConfig(True, "http", "http://127.0.0.1:1/", 0.1, True),
        log=LogConfig(False, 10, 0, 0),
        lifecycle=LifecycleConfig("manual", autostart, "primary", "primary", "status_only"),
        actions=tuple(ActionConfig(a, a, "process_" + a, True) for a in ("start", "stop", "restart")),
        depends_on=deps, group=group, recovery=recovery or RecoveryConfig())


class Registry:
    def __init__(self, services):
        self.services = tuple(services)

    def list_services(self):
        return self.services

    def get(self, sid):
        return next(s for s in self.services if s.id == sid)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds=10):
        self.now += seconds


class Manager:
    def __init__(self):
        self.processes = {}
        self.calls = []
        self.locks = {}
        self.depth = 0
        self.rotations = 0
        self.fail_start = set()
        self.fail_restart = False
        self.before_confirm = None

    def put(self, sid, *, online=True, restarts=0, pid=17):
        self.processes[sid] = Pm2Process(sid, sid, "online" if online else "stopped",
            pid if online else None, 1, restarts, 0, 5)

    @contextmanager
    def service_operation(self, sid):
        with self.locks.setdefault(sid, threading.RLock()):
            self.depth += 1
            try:
                yield
            finally:
                self.depth -= 1

    def snapshot(self):
        return dict(self.processes)

    def get_process(self, sid, *, force=False):
        if force and self.before_confirm:
            callback, self.before_confirm = self.before_confirm, None
            callback()
        return self.processes.get(sid)

    def get_state_readonly(self, sid):
        p = self.processes.get(sid)
        return RuntimeState(pid=p.pid if p else None, create_time=float(p.pid) if p and p.pid else None)

    def start(self, s):
        assert self.depth
        self.calls.append(("start", s.id))
        if s.id in self.fail_start:
            raise RuntimeError("SECRET_COMMAND_OUTPUT")
        self.put(s.id)
        return self.processes[s.id]

    def stop(self, s):
        assert self.depth
        self.calls.append(("stop", s.id))
        self.put(s.id, online=False)

    def restart(self, s):
        assert self.depth
        self.calls.append(("restart", s.id))
        if self.fail_restart:
            raise RuntimeError("SECRET_COMMAND_OUTPUT")
        p = self.processes[s.id]
        self.put(s.id, restarts=p.restart_count + 1, pid=p.pid + 1)

    def rotate_running_logs(self, services):
        assert self.depth
        self.rotations += 1


class Health:
    def __init__(self):
        self.ok = {}
        self.calls = 0
        self.on_probe = None

    def check(self, s, **kwargs):
        assert kwargs["force_health_probe"]
        self.calls += 1
        if self.on_probe:
            self.on_probe(s)
        return SimpleNamespace(alive=True, unmanaged=False, health={"ok": self.ok.get(s.id, True)})


def operations(tmp_path, services):
    registry, manager, health, clock = Registry(services), Manager(), Health(), Clock()
    ops = ServiceOperations(registry, manager, health, tmp_path, clock=clock, monotonic_clock=clock)
    return ops, manager, health, clock


def finish(ops, job):
    ops._job_thread.join(2)
    assert not ops._job_thread.is_alive()
    return ops.job(job["id"])


def test_recovery_threshold_fresh_cadence_and_no_stopped_revival(tmp_path):
    s = service(recovery=RecoveryConfig(enabled=True))
    ops, pm, health, clock = operations(tmp_path, [s])
    pm.put(s.id)
    health.ok[s.id] = False
    original_restart = pm.restart

    def restored(svc):
        original_restart(svc)
        health.ok[svc.id] = True

    pm.restart = restored
    ops.poll_once()
    ops.poll_once()  # same sample cannot count twice
    clock.advance()
    ops.poll_once()
    assert pm.calls == []
    clock.advance()
    ops.poll_once()
    assert pm.calls == [("restart", "one")]
    pm.put(s.id, online=False)
    for _ in range(5):
        clock.advance(100)
        ops.poll_once()
    assert pm.calls == [("restart", "one")]
    assert pm.rotations == 8
    assert "recovery_succeeded" in [e["type"] for e in ops.events(s.id)["events"]]


@pytest.mark.parametrize("enabled,health_enabled,started_at", [(False, True, 1), (True, False, 1), (True, True, 990)])
def test_recovery_disabled_health_disabled_and_startup_grace(tmp_path, enabled, health_enabled, started_at):
    s = service(recovery=RecoveryConfig(enabled=enabled, failure_threshold=1))
    s = replace(s, health=replace(s.health, enabled=health_enabled))
    ops, pm, health, _ = operations(tmp_path, [s])
    pm.put(s.id)
    pm.processes[s.id] = replace(pm.processes[s.id], started_at=started_at)
    health.ok[s.id] = False
    ops.poll_once()
    assert pm.calls == []
    assert health.calls == 0


def test_recovery_succeeds_only_after_health_restored_and_timeout_does_not_retry(tmp_path):
    s = service(recovery=RecoveryConfig(True, 1, 0, 300, 3))
    ops, pm, health, clock = operations(tmp_path, [s])
    pm.put(s.id)
    health.ok[s.id] = False
    probes = 0

    def advance_during_readiness(_service):
        nonlocal probes
        probes += 1
        if probes >= 3:
            clock.advance(31)

    health.on_probe = advance_during_readiness
    ops.poll_once()
    assert pm.calls == [("restart", "one")]
    kinds = [e["type"] for e in ops.events(s.id)["events"]]
    assert "recovery_failed" in kinds
    assert "recovery_succeeded" not in kinds
    clock.advance()
    ops.poll_once()
    assert pm.calls == [("restart", "one")]


def test_recovery_hour_cap_and_cooldown_persist_across_controller_restart(tmp_path):
    s = service(recovery=RecoveryConfig(True, 1, 0, 20, 2))
    ops, pm, health, clock = operations(tmp_path, [s])
    pm.put(s.id)
    original_restart = pm.restart

    def restores(svc):
        original_restart(svc)
        health.ok[svc.id] = True

    pm.restart = restores
    health.ok[s.id] = False
    ops.poll_once()
    for seconds in (10, 10, 10):
        clock.advance(seconds)
        health.ok[s.id] = False
        ops = ServiceOperations(ops.registry, pm, health, tmp_path, clock=clock, monotonic_clock=clock)
        ops.poll_once()
    assert pm.calls == [("restart", "one"), ("restart", "one")]


def test_recovery_manual_stop_identity_change_and_inconclusive_recheck(tmp_path):
    s = service(recovery=RecoveryConfig(enabled=True, failure_threshold=1))
    ops, pm, health, clock = operations(tmp_path, [s])
    pm.put(s.id)
    health.ok[s.id] = False
    pm.before_confirm = lambda: pm.put(s.id, online=False)
    ops.poll_once()
    assert pm.calls == []
    clock.advance()
    pm.put(s.id)
    probes = 0

    def inconclusive(_service):
        nonlocal probes
        probes += 1
        if probes == 2:
            health.ok[s.id] = None

    health.on_probe = inconclusive
    ops.poll_once()
    assert pm.calls == []


def test_failed_restart_has_no_fallback_and_budget_survives_reload(tmp_path):
    s = service(recovery=RecoveryConfig(True, 1, 0, 0, 1))
    ops, pm, health, clock = operations(tmp_path, [s])
    pm.put(s.id)
    pm.fail_restart = True
    health.ok[s.id] = False
    ops.poll_once()
    assert pm.calls == [("restart", s.id)]
    clock.advance()
    reloaded = ServiceOperations(ops.registry, pm, health, tmp_path, clock=clock, monotonic_clock=clock)
    reloaded.poll_once()
    assert pm.calls == [("restart", s.id)]
    assert "SECRET" not in reloaded.path.read_text()
    assert reloaded.events(s.id)["events"][-1]["type"] == "online_observed"
    assert any(e["type"] == "recovery_failed" for e in reloaded.events(s.id)["events"])


def test_history_bounds_private_atomic_file_and_observed_restart(tmp_path):
    s = service()
    ops, pm, _, clock = operations(tmp_path, [s])
    pm.put(s.id)
    ops.poll_once()
    clock.advance()
    pm.put(s.id, restarts=3)
    ops.poll_once()
    result = ops.events(s.id)
    assert result["restart_count"] == 3
    assert result["last_exit_time"] == clock()
    assert result["events"][-1]["type"] == "restart_observed"
    for _ in range(105):
        ops.record_action(s.id, "restart")
    assert len(ops.events(s.id)["events"]) == 100
    assert os.stat(ops.path).st_mode & 0o777 == 0o600
    assert os.stat(ops.path.parent).st_mode & 0o777 == 0o700
    assert len(list(ops.path.parent.iterdir())) == 1
    loaded = ServiceOperations(ops.registry, pm, ops.health, tmp_path)
    assert loaded.events(s.id) == ops.events(s.id)
    assert set(json.loads(ops.path.read_text())[s.id]["events"][0]) == {"id", "at", "type", "message"}


def test_group_topology_reverse_stop_and_restart_keeps_stopped(tmp_path):
    db, web, off = service("db"), service("web", deps=("db",)), service("off")
    ops, pm, _, _ = operations(tmp_path, [web, off, db])
    result = finish(ops, ops.submit_group("apps", "start"))
    assert result["status"] == "succeeded"
    assert pm.calls.index(("start", "db")) < pm.calls.index(("start", "web"))
    pm.put("off", online=False)
    pm.calls.clear()
    result = finish(ops, ops.submit_group("apps", "restart"))
    assert pm.calls == [("stop", "web"), ("stop", "db"), ("start", "db"), ("start", "web")]
    assert {r["service_id"]: r["status"] for r in result["results"]}["off"] == "skipped"


def test_group_failed_prerequisite_blocks_downstream_and_sanitizes_errors(tmp_path):
    ops, pm, _, _ = operations(tmp_path, [service("web", deps=("db",)), service("db")])
    pm.fail_start.add("db")
    result = finish(ops, ops.submit_group("apps", "start"))
    assert result["status"] == "failed"
    assert pm.calls == [("start", "db")]
    assert {r["service_id"]: r["status"] for r in result["results"]} == {"db": "failed", "web": "blocked"}
    assert "SECRET" not in json.dumps(result)


def test_group_never_starts_outside_dependency_and_refuses_used_stop(tmp_path):
    db, web = service("db", group="data"), service("web", deps=("db",))
    ops, pm, _, _ = operations(tmp_path, [web, db])
    result = finish(ops, ops.submit_group("apps", "start"))
    assert result["status"] == "failed"
    assert pm.calls == []
    pm.put("db")
    pm.put("web")
    result = finish(ops, ops.submit_group("data", "stop"))
    assert result["status"] == "failed"
    assert result["results"][0]["message"] == "dependency_used_outside_group"
    assert pm.calls == []


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
@pytest.mark.parametrize("missing", [True, False])
def test_group_respects_missing_and_disabled_controls(tmp_path, action, missing):
    s = service()
    s = replace(s, actions=tuple(a for a in s.actions if a.type != "process_" + action)
                if missing else tuple(replace(a, enabled=False) if a.type == "process_" + action else a
                                      for a in s.actions))
    ops, pm, _, _ = operations(tmp_path, [s])
    pm.put(s.id)
    result = finish(ops, ops.submit_group("apps", action))
    assert result["status"] == "failed"
    assert result["results"][0]["message"] == ("action_not_configured" if missing else "action_disabled")
    assert pm.calls == []


def test_manual_guards_block_dependency_stop_and_do_not_start_outside_dependencies(tmp_path):
    db, web = service("db", group="data"), service("web", deps=("db",))
    ops, pm, _, _ = operations(tmp_path, [web, db])
    with pytest.raises(OperationsError, match="dependency_not_ready"):
        ops.start_service(web)
    assert pm.calls == []
    pm.put("db")
    pm.put("web")
    with pytest.raises(OperationsError, match="dependency_used_outside_group"):
        ops.check_stop_allowed(db)
    assert pm.calls == []


@pytest.mark.parametrize("intervention", ["before_stop", "before_start"])
def test_group_restart_does_not_revive_intervening_manual_stop(tmp_path, intervention):
    s = service()
    ops, pm, _, _ = operations(tmp_path, [s])
    pm.put(s.id)
    original_mutate = ops._mutate
    target_phase = "stop" if intervention == "before_stop" else "start"
    intervened = False

    def manual_stop_before_phase(svc, action, *args, **kwargs):
        nonlocal intervened
        if action == target_phase and not intervened:
            intervened = True
            # Same lock and record ordering used by the parent's manual route.
            with ops._mutation(svc):
                pm.stop(svc)
                ops.record_action(svc.id, "process_stop")
        return original_mutate(svc, action, *args, **kwargs)

    ops._mutate = manual_stop_before_phase
    result = finish(ops, ops.submit_group("apps", "restart"))
    assert intervened
    assert result["status"] == "failed"
    assert result["results"] == [{"service_id": s.id, "status": "failed",
                                   "message": "intervening_manual_action"}]
    assert all(action == "stop" for action, _ in pm.calls)
    assert pm.processes[s.id].status == "stopped"


def test_group_readiness_allows_launching_until_online(tmp_path, monkeypatch):
    s = service()
    ops, pm, health, clock = operations(tmp_path, [s])
    original_start = pm.start

    def launching(svc):
        original_start(svc)
        pm.processes[svc.id] = replace(pm.processes[svc.id], status="launching")
        return pm.processes[svc.id]

    waits = []

    def advance_to_online(seconds):
        waits.append(seconds)
        clock.advance(seconds)
        pm.put(s.id)
        return False

    pm.start = launching
    monkeypatch.setattr(ops._stop, "wait", advance_to_online)
    result = finish(ops, ops.submit_group("apps", "start"))
    assert result["status"] == "succeeded"
    assert pm.calls == [("start", s.id)]
    assert len(waits) == 1
    assert health.calls == 1


@pytest.mark.parametrize("status", ["stopped", "errored"])
def test_readiness_rejects_stopped_or_errored_without_restart(tmp_path, status):
    s = service()
    ops, pm, health, _ = operations(tmp_path, [s])
    pm.put(s.id, online=False)
    pm.processes[s.id] = replace(pm.processes[s.id], status=status)
    with pytest.raises(OperationsError, match="service_no_longer_online"):
        ops._wait_ready(s)
    assert pm.calls == []
    assert health.calls == 0


def test_intent_disk_failure_blocks_stop_before_any_process_mutation(tmp_path, monkeypatch):
    s = service()
    ops, pm, health, clock = operations(tmp_path, [s])
    pm.put(s.id)
    ops.record_action(s.id, "start")
    def full_disk():
        raise OSError("SECRET_STORAGE_PATH")
    monkeypatch.setattr(ops, "_persist", full_disk)
    result = finish(ops, ops.submit_group("apps", "stop"))
    assert result["results"][0]["message"] == "intent_persistence_failed"
    assert pm.calls == []
    assert pm.processes[s.id].alive
    assert ops.recovery_status(s.id)["reason"] == "intent_persistence_failed"
    reloaded = ServiceOperations(ops.registry, pm, health, tmp_path, clock=clock, monotonic_clock=clock)
    assert reloaded.recovery_status(s.id)["desired_state"] == "running"


def test_definition_change_blocks_queued_mutation_and_jobs_bounded(tmp_path):
    s = service()
    ops, pm, _, _ = operations(tmp_path, [s])
    original = ops._run_job

    def changed(job_id, ordered):
        ops.registry.services = (replace(s, command=("different",)),)
        original(job_id, ordered)

    ops._run_job = changed
    result = finish(ops, ops.submit_group("apps", "start"))
    assert result["error"] == "definition_changed"
    assert pm.calls == []
    ops._run_job = original
    ops.JOB_LIMIT = 2
    for _ in range(3):
        finish(ops, ops.submit_group("apps", "start"))
    assert len(ops._jobs) == 2


def test_autostart_does_not_start_nonselected_dependency(tmp_path):
    db = service("db", autostart=False)
    web = service("web", deps=("db",), autostart=True)
    ops, pm, _, _ = operations(tmp_path, [web, db])
    ops.autostart()
    assert pm.calls == []
    pm.put("db")
    ops.autostart()
    assert pm.calls == [("start", "web")]


def test_maintenance_prevents_recovery_and_rotation_and_worker_shutdown(tmp_path):
    ops, pm, health, _ = operations(tmp_path, [service(recovery=RecoveryConfig(True, 1))])
    pm.put("one")
    health.ok["one"] = False
    marker = tmp_path / "pm2-engine" / "recovery.json"
    marker.parent.mkdir()
    marker.write_text("{}")
    with pytest.raises(OperationsError, match="maintenance"):
        ops.poll_once()
    assert pm.calls == []
    assert pm.rotations == 0
    ops.start()
    ops.shutdown()
    assert not ops._thread.is_alive()


def test_routes_auth_csrf_native_and_no_get_mutation(tmp_path, monkeypatch):
    from flask import Flask
    from auth import init_app, login_session
    from routes.operations import bp
    monkeypatch.setenv("CONTROL_PASSWORD", "secret")
    ops, pm, _, _ = operations(tmp_path, [service()])
    app = Flask(__name__)
    init_app(app, secret_key_path=tmp_path / "secret")
    app.config["service_operations"] = ops
    app.register_blueprint(bp)

    @app.post("/login-test")
    def login():
        return {"csrf": login_session()}

    client = app.test_client()
    assert client.get("/api/services/one/events").status_code == 401
    assert client.get("/api/service-operations/jobs/nope").status_code == 401
    assert client.post("/api/service-groups/apps/actions/start", json={}).status_code == 401
    token = client.post("/login-test").json["csrf"]
    assert client.get("/api/services/one/events").status_code == 200
    assert pm.calls == []
    assert client.post("/api/service-groups/apps/actions/start", json={}).status_code == 403
    response = client.post("/api/service-groups/apps/actions/start", json={}, headers={"X-CSRF-Token": token})
    assert response.status_code == 202
    result = finish(ops, response.json["job"])
    assert client.get(f'/api/service-operations/jobs/{result["id"]}').json == {"job": result}
    ops.supported = False
    response = client.post("/api/service-groups/apps/actions/start", headers={"X-CSRF-Token": token})
    assert response.status_code == 409
    assert response.json["supported"] is False


def daemon_operations(tmp_path, services):
    ops, pm, health, clock = operations(tmp_path, services)
    pm.boot = "101:1"
    pm.recovery_snapshot = lambda: (dict(pm.processes), pm.boot)
    pm.daemon_identity = lambda: pm.boot
    pm.runtime_identity_alive = lambda *_: False
    pm.recovery_process_conflict = lambda _: False
    return ops, pm, health, clock


def test_daemon_replacement_restores_only_running_intent_in_dependency_order(tmp_path, monkeypatch):
    monkeypatch.setattr("net_utils.find_port_holder", lambda _: None)
    db = replace(service("db"), port=12340)
    web = replace(service("web", deps=("db",)), port=12341)
    stopped = replace(service("stopped"), port=12342)
    ops, pm, _, clock = daemon_operations(tmp_path, [web, stopped, db])
    pm.put("db")
    pm.put("web")
    pm.put("stopped", online=False)
    ops.poll_once()
    pm.processes.clear()
    pm.boot = "102:2"
    clock.advance()
    ops.poll_once()
    assert pm.calls == [("start", "db"), ("start", "web")]
    assert ops.recovery_status("stopped")["desired_state"] == "stopped"
    assert ops.recovery_status("web")["status"] == "restored"
    assert ops.events("web")["events"][-1]["type"] == "daemon_restore_succeeded"


def test_durable_daemon_intent_survives_controller_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("net_utils.find_port_holder", lambda _: None)
    s = replace(service(), port=12340)
    ops, pm, health, clock = daemon_operations(tmp_path, [s])
    pm.put(s.id)
    ops.poll_once()
    pm.processes.clear()
    pm.boot = "103:3"
    reloaded = ServiceOperations(ops.registry, pm, health, tmp_path, clock=clock, monotonic_clock=clock)
    reloaded.poll_once()
    assert pm.calls == [("start", s.id)]
    assert reloaded.recovery_status(s.id)["status"] == "restored"


@pytest.mark.parametrize("blocker", ["port", "orphan", "uncertain", "no_port", "definition"])
def test_daemon_restore_blocks_unsafe_duplicate_or_changed_definition(tmp_path, monkeypatch, blocker):
    monkeypatch.setattr("net_utils.find_port_holder", lambda _: {"pid": 55} if blocker == "port" else None)
    s = replace(service(), port=None if blocker == "no_port" else 12340)
    ops, pm, _, clock = daemon_operations(tmp_path, [s])
    pm.put(s.id)
    ops.poll_once()
    pm.processes.clear()
    pm.boot = "104:4"
    if blocker in {"orphan", "uncertain"}:
        pm.runtime_identity_alive = lambda *_: True if blocker == "orphan" else None
    if blocker == "definition":
        ops.registry.services = (replace(s, command=("changed",)),)
    clock.advance()
    ops.poll_once()
    assert pm.calls == []
    assert ops.recovery_status(s.id)["status"] == "blocked"
    clock.advance()
    ops.poll_once()
    assert [e["type"] for e in ops.events(s.id)["events"]].count("daemon_restore_blocked") == 1


def test_daemon_port_conflict_can_clear_but_manual_stop_cancels_recovery(tmp_path, monkeypatch):
    s = replace(service(), port=12340)
    ops, pm, _, clock = daemon_operations(tmp_path, [s])
    pm.put(s.id)
    ops.poll_once()
    pm.processes.clear()
    pm.boot = "105:5"
    monkeypatch.setattr("net_utils.find_port_holder", lambda _: {"pid": 55})
    clock.advance()
    ops.poll_once()
    assert ops.recovery_status(s.id)["status"] == "blocked"
    ops.record_action(s.id, "stop")
    monkeypatch.setattr("net_utils.find_port_holder", lambda _: None)
    clock.advance()
    ops.poll_once()
    assert pm.calls == []
    assert ops.recovery_status(s.id) == {"desired_state": "stopped", "status": None, "reason": None}


def test_same_daemon_missing_or_stopped_process_is_not_revived(tmp_path):
    s = replace(service(), port=12340)
    ops, pm, _, clock = daemon_operations(tmp_path, [s])
    pm.put(s.id)
    ops.poll_once()
    pm.processes.clear()
    clock.advance()
    ops.poll_once()
    pm.put(s.id, online=False)
    clock.advance()
    ops.poll_once()
    assert pm.calls == []


def test_daemon_restore_failure_retry_budget_persists(tmp_path, monkeypatch):
    monkeypatch.setattr("net_utils.find_port_holder", lambda _: None)
    s = replace(service(), port=12340)
    ops, pm, health, clock = daemon_operations(tmp_path, [s])
    pm.put(s.id)
    ops.poll_once()
    pm.processes.clear()
    pm.fail_start.add(s.id)
    pm.boot = "106:6"
    for _ in range(5):
        clock.advance(60)
        ops = ServiceOperations(ops.registry, pm, health, tmp_path, clock=clock, monotonic_clock=clock)
        ops.poll_once()
    assert pm.calls == [("start", s.id)] * 3
    assert ops.recovery_status(s.id)["status"] == "failed"
    assert "SECRET" not in ops.path.read_text()


def test_durable_manual_stop_overrides_autostart_after_controller_restart(tmp_path):
    s = service(autostart=True)
    ops, pm, health, clock = operations(tmp_path, [s])
    ops.record_action(s.id, "stop")
    reloaded = ServiceOperations(ops.registry, pm, health, tmp_path, clock=clock, monotonic_clock=clock)
    reloaded.autostart()
    assert pm.calls == []


def test_manual_stop_during_daemon_readiness_cancels_restoration(tmp_path, monkeypatch):
    monkeypatch.setattr("net_utils.find_port_holder", lambda _: None)
    s = replace(service(), port=12340)
    ops, pm, health, clock = daemon_operations(tmp_path, [s])
    pm.put(s.id)
    ops.poll_once()
    pm.processes.clear()
    pm.boot = "107:7"
    def stop_during_probe(svc):
        health.on_probe = None
        pm.stop(svc)
        ops.record_action(svc.id, "stop")
    health.on_probe = stop_during_probe
    clock.advance()
    ops.poll_once()
    assert ops.recovery_status(s.id)["desired_state"] == "stopped"
    assert ops.recovery_status(s.id)["status"] is None
    assert not any(e["type"] == "daemon_restore_succeeded" for e in ops.events(s.id)["events"])


def test_newer_nonlistening_orphan_blocks_daemon_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr("net_utils.find_port_holder", lambda _: None)
    s = replace(service(), port=12340)
    ops, pm, _, clock = daemon_operations(tmp_path, [s])
    pm.put(s.id, pid=17)
    ops.poll_once()
    # The saved PID died, but PM2 started a different app before itself died.
    pm.processes.clear()
    pm.boot = "108:8"
    pm.runtime_identity_alive = lambda *_: False
    pm.recovery_process_conflict = lambda _: True
    clock.advance()
    ops.poll_once()
    assert pm.calls == []
    assert ops.recovery_status(s.id)["reason"] == "previous_process_unconfirmed"


def test_autostart_selected_before_manual_stop_does_not_override_it(tmp_path):
    import app as backend_app
    s = service(autostart=True)
    ops, pm, _, _ = operations(tmp_path, [s])
    selected, release = threading.Event(), threading.Event()
    original = ops._mutate
    def paused(*args, **kwargs):
        selected.set()
        assert release.wait(2)
        return original(*args, **kwargs)
    ops._mutate = paused
    # Exercise the app's startup lifecycle rather than invoking observer directly.
    pm.supports_adoption = False
    thread = threading.Thread(target=backend_app._run_startup_lifecycle, args=(ops.registry, pm, ops))
    thread.start()
    assert selected.wait(1)
    ops.cancel_recovery(s)
    release.set()
    thread.join(3)
    ops.shutdown()
    assert not thread.is_alive()
    assert pm.calls == []
    assert ops.recovery_status(s.id)["desired_state"] == "stopped"


@pytest.mark.parametrize("raw", ['not-json', '[]', '{"one":{"desired_state":[]}}', '{"one":false}'])
def test_corrupt_intent_blocks_autostart_and_preserves_original_file(tmp_path, raw):
    s = service(autostart=True)
    ops, pm, health, clock = operations(tmp_path, [s])
    ops.path.parent.mkdir(parents=True)
    ops.path.write_text(raw)
    reloaded = ServiceOperations(ops.registry, pm, health, tmp_path, clock=clock, monotonic_clock=clock)
    reloaded.autostart()
    assert pm.calls == []
    assert reloaded.recovery_status(s.id)["reason"] == "intent_state_unreadable"
    assert ops.path.read_text() == raw


def test_legacy_stop_event_and_pm2_stopped_membership_migrate_before_autostart(tmp_path):
    a, b = service("a", autostart=True), service("b", autostart=True)
    ops, pm, health, clock = operations(tmp_path, [a, b])
    ops.path.parent.mkdir(parents=True)
    ops.path.write_text(json.dumps({"a":{"events":[{"type":"stop", "at":900}]}}))
    pm.put("b", online=False)
    reloaded = ServiceOperations(ops.registry, pm, health, tmp_path, clock=clock, monotonic_clock=clock)
    reloaded.autostart()
    assert pm.calls == []
    assert all(reloaded.recovery_status(s.id)["desired_state"] == "stopped" for s in (a,b))


def test_stop_intent_survives_process_failure_and_blocks_health_recovery(tmp_path):
    s = service(recovery=RecoveryConfig(True, 1))
    ops, pm, health, clock = operations(tmp_path, [s])
    pm.put(s.id)
    health.ok[s.id] = False
    ops.prepare_intent(s, "stop")
    reloaded = ServiceOperations(ops.registry, pm, health, tmp_path, clock=clock, monotonic_clock=clock)
    reloaded.poll_once()
    assert pm.calls == []
    assert reloaded.recovery_status(s.id)["desired_state"] == "stopped"


def test_group_stop_with_unmanaged_process_cannot_report_success(tmp_path):
    from pm2_manager import Pm2Error
    s = service()
    ops, pm, _, _ = operations(tmp_path, [s])
    def external(_):
        raise Pm2Error("external_process_unmanaged")
    pm.assert_operation_ownership = external
    result = finish(ops, ops.submit_group("apps", "stop"))
    assert result["status"] == "failed"
    assert pm.calls == []


def test_stop_intent_arriving_during_health_probe_blocks_restart(tmp_path):
    s = service(recovery=RecoveryConfig(True,1))
    ops, pm, health, _ = operations(tmp_path,[s])
    pm.put(s.id)
    health.ok[s.id] = False
    def cancel(_):
        health.on_probe = None
        ops.cancel_recovery(s)
    health.on_probe=cancel
    ops.poll_once()
    assert pm.calls == []


def test_observation_only_switch_blocks_all_automatic_start_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTROL_PM2_AUTOMATIC_START','0')
    s=service(autostart=True,recovery=RecoveryConfig(True,1))
    ops,pm,health,_=operations(tmp_path,[s])
    ops.autostart()
    assert pm.calls == []
    pm.put(s.id)
    health.ok[s.id]=False
    ops.poll_once()
    assert pm.calls == []
