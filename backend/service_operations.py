"""Bounded service observations and explicit PM2 orchestration.

PM2 remains the process owner. The optional observer never revives stopped
processes and only requests a restart after fresh, identity-checked health probes.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
import psutil

from routes.config_api import _config_reconcile_lock


_log = logging.getLogger("server_control.operations")
_MESSAGES = {
    "external_takeover_requested": "External instance replacement requested; its work may be interrupted.",
    "external_takeover_succeeded": "External instance replaced by a healthy PM2 managed process.",
    "external_takeover_failed": "External instance replacement failed; inspect current service state.",
    "start": "Service start requested successfully.",
    "stop": "Service stop requested successfully.",
    "restart": "Service restart requested successfully.",
    "online_observed": "PM2 reported the service online.",
    "stopped_observed": "PM2 reported the service no longer online.",
    "restart_observed": "PM2 restart count increased; cause unavailable.",
    "recovery_requested": "Unhealthy recovery restart requested.",
    "recovery_succeeded": "Unhealthy recovery restart completed.",
    "recovery_failed": "Unhealthy recovery restart failed; no fallback attempted.",
    "daemon_restore_requested": "Restoring previously running service after PM2 daemon replacement.",
    "daemon_restore_succeeded": "Service restored and readiness confirmed after PM2 daemon replacement.",
    "daemon_restore_blocked": "PM2 recovery blocked; check external process, dependencies and configuration.",
    "daemon_restore_failed": "PM2 recovery failed; inspect service health and logs.",
}
_DAEMON_RESTORE_REASONS = frozenset({
    "definition_changed", "existing_pm2_entry", "previous_process_unconfirmed",
    "external_port_occupied", "dependency_not_ready", "dependency_missing",
    "pm2_start_failed", "readiness_failed", "automatic_recovery_disabled",
    "external_takeover_not_allowed",
})


def _definition_id(service):
    # Bind intent to its exact definition without persisting commands or secrets.
    definition = asdict(service)
    # This opt-in changes recovery policy, not the bound launch definition.
    definition["recovery"].pop("auto_takeover", None)
    return hashlib.sha256(json.dumps(definition, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


class OperationsError(RuntimeError):
    """Safe, public error code; never contains command output."""


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def dependency_order(services):
    """Order only selected definitions; outside dependencies are checked, not added."""
    index = {s.id: s for s in services}
    ordered, visiting, visited = [], set(), set()

    def visit(sid):
        if sid in visiting:
            raise OperationsError("dependency_cycle")
        if sid in visited:
            return
        visiting.add(sid)
        for dep in getattr(index[sid], "depends_on", ()):
            if dep in index:
                visit(dep)
        visiting.remove(sid)
        visited.add(sid)
        ordered.append(index[sid])

    for sid in index:
        visit(sid)
    return ordered


class ServiceOperations:
    EVENT_LIMIT = 100
    JOB_LIMIT = 50
    POLL_SECONDS = 10.0
    READY_SECONDS = 30.0

    def __init__(self, registry, process_manager, health_checker, runtime_dir,
                 *, clock=time.time, monotonic_clock=time.monotonic):
        self.registry = registry
        self.pm = process_manager
        self.health = health_checker
        self.runtime = Path(runtime_dir)
        self.path = self.runtime / "service-operations" / "events.json"
        self.clock = clock
        self.monotonic = monotonic_clock
        self.supported = callable(getattr(self.pm, "snapshot", None)) and callable(
            getattr(self.pm, "service_operation", None))
        self._lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._job_thread = None
        self._last_poll = None
        self._observed = {}
        self._failures = {}
        self._jobs = OrderedDict()
        self._action_generations = {}
        self._job_intents = {}
        self._state_error = None
        self._automatic_enabled = os.environ.get("CONTROL_PM2_AUTOMATIC_START", "1") != "0"
        self._history = self._load()

    def _load(self):
        """Accept only bounded typed fields, including when loading old/private files."""
        try:
            # Bound corrupt input before parsing; normally this file is much smaller.
            if self.path.stat().st_size > 8 * 1024 * 1024:
                self._state_error = "intent_state_unreadable"
                return {}
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            self._state_error = "intent_state_unreadable"
            return {}
        if not isinstance(raw, dict):
            self._state_error = "intent_state_unreadable"
            return {}
        if type(raw.get("_schema_version", 1)) is not int or raw.get("_schema_version", 1) != 1:
            self._state_error = "intent_state_unreadable"
            return {}
        result = {}
        for service in self.registry.list_services():
            item = raw.get(service.id)
            if not isinstance(item, dict):
                if service.id in raw:
                    self._state_error = "intent_state_unreadable"
                continue
            # Missing fields are valid legacy state; present safety fields must
            # never be discarded or repaired into a less restrictive default.
            valid = {
                "definition_id": lambda v: isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v),
                "daemon_id": lambda v: isinstance(v, str) and 0 < len(v) <= 128,
                "owned_pid": lambda v: type(v) is int and 0 < v <= 2**31 - 1,
                "owned_created_at": lambda v: _number(v) and v > 0,
                "daemon_restore_attempts": lambda v: type(v) is int and 0 <= v <= 3,
                "daemon_restore_at": lambda v: _number(v) and v >= 0,
                "last_recovery_attempt": lambda v: v is None or (_number(v) and v >= 0),
                "recovery_attempts": lambda v: isinstance(v, list) and all(_number(t) and t >= 0 for t in v),
                "daemon_restore_status": lambda v: v in {"pending", "restoring", "restored", "blocked", "failed"} if isinstance(v, str) else False,
                "daemon_restore_reason": lambda v: isinstance(v, str) and v in _DAEMON_RESTORE_REASONS,
                "startup_blocked_reason": lambda v: v in {"previous_process_unconfirmed", "definition_changed", "automatic_recovery_disabled", "automatic_start_budget_exhausted", "dependency_not_ready", "readiness_failed"} if isinstance(v, str) else False,
            }
            if any(key in item and not check(item[key]) for key, check in valid.items()):
                self._state_error = "intent_state_unreadable"
                continue
            events = []
            source = item.get("events", [])
            for event in source[-self.EVENT_LIMIT:] if isinstance(source, list) else []:
                if not isinstance(event, dict) or not isinstance(event.get("type"), str) or event.get("type") not in _MESSAGES:
                    continue
                if not _number(event.get("at")):
                    continue
                clean = {"id": uuid.uuid4().hex, "at": event["at"],
                         "type": event["type"], "message": _MESSAGES[event["type"]]}
                eid = event.get("id")
                if isinstance(eid, str) and len(eid) == 32 and all(c in "0123456789abcdef" for c in eid):
                    clean["id"] = eid
                for key in ("exit_code", "restart_count"):
                    if type(event.get(key)) is int:
                        clean[key] = event[key]
                events.append(clean)
            clean_item = {"events": events, "restart_count": 0,
                          "last_exit_code": None, "last_exit_time": None, "recovery_attempts": [],
                          "last_recovery_attempt": None}
            if isinstance(item.get("desired_state"), str) and item.get("desired_state") in {"running", "stopped"}:
                clean_item["desired_state"] = item["desired_state"]
            elif "desired_state" in item:
                self._state_error = "intent_state_unreadable"
            else:
                for event in reversed(events):
                    if event["type"] in {"start", "stop", "restart"}:
                        clean_item["desired_state"] = "stopped" if event["type"] == "stop" else "running"
                        clean_item["definition_id"] = _definition_id(service)
                        break
            for key in ("definition_id", "daemon_id"):
                value = item.get(key)
                if isinstance(value, str) and len(value) <= 128:
                    clean_item[key] = value
            if isinstance(item.get("daemon_restore_status"), str) and item.get("daemon_restore_status") in {"pending", "restoring", "restored", "blocked", "failed"}:
                clean_item["daemon_restore_status"] = item["daemon_restore_status"]
            if isinstance(item.get("daemon_restore_reason"), str) and item["daemon_restore_reason"] in _DAEMON_RESTORE_REASONS:
                clean_item["daemon_restore_reason"] = item["daemon_restore_reason"]
            if _number(item.get("daemon_restore_at")):
                clean_item["daemon_restore_at"] = item["daemon_restore_at"]
            if isinstance(item.get("startup_blocked_reason"), str) and item["startup_blocked_reason"] in {
                "previous_process_unconfirmed", "definition_changed", "automatic_recovery_disabled",
                "automatic_start_budget_exhausted", "dependency_not_ready", "readiness_failed",
            }:
                clean_item["startup_blocked_reason"] = item["startup_blocked_reason"]
            if type(item.get("daemon_restore_attempts")) is int:
                clean_item["daemon_restore_attempts"] = item["daemon_restore_attempts"]
            if type(item.get("owned_pid")) is int and item["owned_pid"] > 0:
                clean_item["owned_pid"] = item["owned_pid"]
            if _number(item.get("owned_created_at")):
                clean_item["owned_created_at"] = item["owned_created_at"]
            for key in ("restart_count", "last_exit_code"):
                if type(item.get(key)) is int:
                    clean_item[key] = item[key]
            if _number(item.get("last_exit_time")):
                clean_item["last_exit_time"] = item["last_exit_time"]
            if _number(item.get("last_recovery_attempt")):
                clean_item["last_recovery_attempt"] = item["last_recovery_attempt"]
            attempts = item.get("recovery_attempts", [])
            if isinstance(attempts, list):
                clean_item["recovery_attempts"] = [t for t in attempts[-100:]
                                                    if _number(t) and t > self.clock() - 3600]
            result[service.id] = clean_item
        return result

    def _entry(self, sid):
        return self._history.setdefault(sid, {"events": [], "restart_count": 0,
            "last_exit_code": None, "last_exit_time": None, "recovery_attempts": [],
            "last_recovery_attempt": None})

    def _persist(self):
        """Called under _lock; replace a private file without exposing partial JSON."""
        if self._state_error:
            raise OSError("intent state unavailable; preserve original file")
        active = {s.id for s in self.registry.list_services()}
        self._history = {sid: entry for sid, entry in self._history.items() if sid in active}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        fd, name = tempfile.mkstemp(prefix=".events-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({**self._history, "_schema_version": 1}, stream, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _event(self, sid, kind, **fields):
        event = {"id": uuid.uuid4().hex, "at": self.clock(), "type": kind,
                 "message": _MESSAGES[kind]}
        event.update({key: value for key, value in fields.items()
                      if key in {"exit_code", "restart_count"} and type(value) is int})
        entry = self._entry(sid)
        entry["events"].append(event)
        del entry["events"][:-self.EVENT_LIMIT]

    def events(self, sid):
        self.registry.get(sid)
        with self._lock:
            entry = self._entry(sid)
            return deepcopy({key: entry[key] for key in
                ("events", "restart_count", "last_exit_code", "last_exit_time")})

    def record_action(self, service_id, action, *, explicit=True):
        """Hook for successful explicit actions; callers must not pass raw errors."""
        action = {"process_start": "start", "process_stop": "stop",
                  "process_restart": "restart"}.get(action, action)
        if action not in {"start", "stop", "restart"}:
            return
        with self._lock:
            # Parent calls this while still holding config reconciliation after
            # the successful mutation. Keep intent invalidation ahead of disk I/O.
            if explicit:
                self._action_generations[service_id] = self._action_generations.get(service_id, 0) + 1
            self._event(service_id, action)
            entry = self._entry(service_id)
            entry["desired_state"] = "stopped" if action == "stop" else "running"
            entry["definition_id"] = _definition_id(self.registry.get(service_id))
            identity = getattr(self.pm, "daemon_identity", lambda: None)()
            if identity is not None:
                entry["daemon_id"] = identity
            entry.pop("daemon_restore_status", None)
            entry.pop("daemon_restore_reason", None)
            entry.pop("startup_blocked_reason", None)
            if explicit:
                entry["daemon_restore_attempts"] = 0
            if action != "stop":
                self._remember_identity(service_id, entry)
            self._failures.pop(service_id, None)
            try:
                self._persist()
            except OSError:
                _log.warning("Service action succeeded but history persistence failed (details withheld)")

    def prepare_intent(self, service, action, *, automatic_reservation=False):
        """Write intent before signaling/spawning; failed writes forbid mutation."""
        with self._lock:
            if self._state_error:
                raise OperationsError(self._state_error)
            entry = self._entry(service.id)
            entry["desired_state"] = "stopped" if action == "stop" else "running"
            entry["definition_id"] = _definition_id(service)
            self._action_generations[service.id] = self._action_generations.get(service.id, 0) + 1
            entry.pop("daemon_restore_status", None)
            entry.pop("daemon_restore_reason", None)
            entry.pop("startup_blocked_reason", None)
            self._failures.pop(service.id, None)
            entry["daemon_restore_attempts"] = 3 if automatic_reservation else 0
            if automatic_reservation:
                entry["daemon_restore_at"] = self.clock()
            else:
                entry.pop("daemon_restore_at", None)
            try:
                self._persist()
            except OSError as exc:
                # Do not mutate after a failed commit. Also block this controller's
                # automatic operations; its on-disk old state is unchanged.
                self._state_error = "intent_persistence_failed"
                raise OperationsError(self._state_error) from exc

    def cancel_recovery(self, service):
        """Persist stop intent without sending any signal to an external app."""
        with self._mutation(service):
            self.prepare_intent(service, "stop")
            self._event(service.id, "stop")
            self._persist()

    def take_over_external(self, service, *, pid, created_at, automatic=False, daemon_id=None):
        """Replace one confirmed external lifetime; PM2 stays the sole supervisor.

        Persist stopped intent before killing, then running intent before spawn.
        A failure is visible and never falls back to an unmanaged spawn.
        """
        if type(pid) is not int or pid <= 0 or not _number(created_at) or created_at <= 0:
            raise OperationsError("external_identity_required")
        with self._mutation(service):
            try:
                with self.pm.external_takeover_operation():
                    return self._take_over_external_locked(service, pid, created_at,
                                                          automatic=automatic, daemon_id=daemon_id)
            except OSError as exc:
                raise OperationsError("managed_operation_busy") from exc

    def _take_over_external_locked(self, service, pid, created_at, *, automatic, daemon_id):
        sid = service.id
        if not service.health.enabled or service.port is None:
            raise OperationsError("takeover_requires_health_and_port")
        if not any(a.type == "process_start" and a.enabled for a in service.actions):
            raise OperationsError("start_action_disabled")
        self._dependencies_ready(service)
        self.check_stop_allowed(service)
        current = self.pm.get_process(sid, force=True)
        captured_daemon = self.pm.daemon_identity()
        if captured_daemon is None:
            raise OperationsError("daemon_identity_unavailable")
        if current is not None:
            # Do not repurpose a stopped/errored entry or race a managed start.
            raise OperationsError("existing_pm2_entry")
        observed = self.health.check(service, include_port_check=False, force_health_probe=True)
        evaluation = self.pm.evaluate_external_identity(service)
        candidate = evaluation.candidate
        if (observed.state != "running_external" or not evaluation.diagnostics.ok or candidate is None
                or candidate.pid != pid or candidate.create_time is None
                or abs(candidate.create_time - created_at) >= 1e-6):
            raise OperationsError("external_identity_changed")
        try:
            if psutil.Process(pid).uids().real != os.getuid():
                raise OperationsError("external_identity_changed")
        except (psutil.Error, OSError) as exc:
            raise OperationsError("external_identity_changed") from exc
        from process_manager import _verify_destructive_identity
        _verify_destructive_identity(service, pid, created_at)
        # Validate the full launch manifest (cwd/interpreter/env) before downtime.
        self.pm.build_manifest(service)
        if automatic:
            self._authorize_automatic_takeover(service, candidate, daemon_id)
        if self.pm.daemon_identity() != captured_daemon:
            raise OperationsError("daemon_identity_unavailable")
        self.prepare_intent(service, "stop", automatic_reservation=automatic)
        with self._lock:
            self._event(sid, "external_takeover_requested")
            self._persist()
        phase = "external_termination_failed"
        try:
            killed = self.pm.kill_external(service, expected_pid=pid, expected_create_time=created_at,
                                           expected_daemon_id=captured_daemon)
            phase = "pm2_start_failed"
            self.pm.snapshot(force=True)
            if self.pm.daemon_identity() != captured_daemon:
                raise OperationsError("daemon_identity_unavailable")
            # Kill helper proves exit/port release; start rechecks occupancy again.
            self.prepare_intent(service, "start", automatic_reservation=automatic)
            self.pm.start(service)
            self.record_action(sid, "start", explicit=not automatic)
            phase = "readiness_failed"
            self._wait_ready(service)
        except Exception as exc:
            with self._lock:
                self._event(sid, "external_takeover_failed")
                self._persist()
            raise OperationsError(phase) from exc
        with self._lock:
            self._event(sid, "external_takeover_succeeded")
            self._persist()
        return killed

    def _authorize_automatic_takeover(self, service, candidate, daemon_id):
        """Only an opted-in, recently orphaned previously owned service may die."""
        if not self._automatic_enabled or not service.recovery.auto_takeover:
            raise OperationsError("external_takeover_not_allowed")
        with self._lock:
            entry = self._entry(service.id)
            if (entry.get("desired_state") != "running"
                    or entry.get("definition_id") != _definition_id(service)
                    or entry.get("daemon_restore_status") not in {"pending", "blocked", "restoring"}
                    or entry.get("daemon_restore_attempts", 0) != 0
                    or not entry.get("owned_pid") or not entry.get("owned_created_at")):
                raise OperationsError("external_takeover_not_allowed")
            if self.pm.daemon_identity() != daemon_id:
                raise OperationsError("external_takeover_not_allowed")
            try:
                boot = float(daemon_id.split(":", 1)[1])
            except (ValueError, TypeError, AttributeError):
                raise OperationsError("external_takeover_not_allowed")
            # A new PID must have been born during the shutdown preceding this
            # boot. An exact old owned lifetime may also survive the shutdown.
            exact_owned = candidate.pid == entry["owned_pid"] and abs(candidate.create_time - entry["owned_created_at"]) < 1e-6
            born_during_exit = boot - 30 <= candidate.create_time <= boot
            if (not 0 <= self.clock() - boot <= 300 or not (exact_owned or born_during_exit)
                    or candidate.create_time < entry["owned_created_at"]):
                raise OperationsError("external_takeover_not_allowed")

    def _maintenance(self):
        root = self.runtime / "pm2-engine"
        # maintenance.lock is a persistent kernel-lock file, not an active marker.
        return (root / "maintenance-owner").exists() or (root / "recovery.json").exists()

    def _available(self):
        if not self.supported:
            raise OperationsError("pm2_required")
        if self._stop.is_set():
            raise OperationsError("operations_shutdown")
        if self._maintenance():
            raise OperationsError("engine_maintenance_or_recovery_required")

    @contextmanager
    def _mutation(self, service):
        # Same order as config reconciliation. Lock dependencies/dependents as well
        # so manual stop cannot invalidate the dependency preflight mid-mutation.
        with _config_reconcile_lock, ExitStack() as stack:
            self._available()
            definitions = self.registry.list_services()
            for sid in sorted(s.id for s in definitions):
                stack.enter_context(self.pm.service_operation(sid))
            if self.registry.get(service.id) != service:
                raise OperationsError("definition_changed")
            yield

    def _online(self, sid):
        process = self.pm.get_process(sid, force=True)
        return process is not None and process.status == "online" and process.pid is not None

    def _health_ok(self, service, timeout=None):
        if not service.health.enabled:
            return True
        probe_service = service
        if timeout is not None:
            probe_service = replace(service, health=replace(service.health,
                timeout_seconds=max(0.01, min(service.health.timeout_seconds, timeout))))
        result = self.health.check(probe_service, include_port_check=False, force_health_probe=True)
        return result.alive and not result.unmanaged and result.health.get("ok") is True

    def _dependencies_ready(self, service, bound=None):
        for sid in getattr(service, "depends_on", ()):
            try:
                dependency = self.registry.get(sid)
            except KeyError as exc:
                raise OperationsError("dependency_missing") from exc
            if bound is not None and sid in bound and dependency != bound[sid]:
                raise OperationsError("definition_changed")
            if not self._online(sid) or not self._health_ok(dependency, self.READY_SECONDS):
                raise OperationsError("dependency_not_ready")

    def check_dependencies_ready(self, service):
        """Manual-start preflight; caller holds config reconciliation through start.

        This never starts dependencies. OperationsError is a safe public code.
        """
        if self.supported:
            with self._mutation(service):
                self._dependencies_ready(service)

    def start_service(self, service):
        """Atomic manual start and prerequisite check; parent records the action."""
        if not self.supported:
            return self.pm.start(service)
        with self._mutation(service):
            self._dependencies_ready(service)
            return self.pm.start(service)

    def check_stop_allowed(self, service):
        """Manual stop/restart preflight; online dependents must be stopped first."""
        if self.supported:
            with self._mutation(service):
                self._refuse_external_dependents(service, {service.id})

    @staticmethod
    def _require_action(service, action):
        control = next((a for a in service.actions if a.type == "process_" + action), None)
        if control is None:
            raise OperationsError("action_not_configured")
        if not control.enabled:
            raise OperationsError("action_disabled")

    def _check_intent(self, service, intent_versions):
        if intent_versions is not None:
            with self._lock:
                if self._action_generations.get(service.id, 0) != intent_versions[service.id]:
                    raise OperationsError("intervening_manual_action")

    def _wait_ready(self, service, *, intent_versions=None):
        deadline = self.monotonic() + self.READY_SECONDS
        while not self._stop.is_set():
            with self._mutation(service):
                self._check_intent(service, intent_versions)
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    raise OperationsError("health_readiness_timeout")
                process = self.pm.get_process(service.id, force=True)
                if process is None or process.status not in {"online", "launching"}:
                    raise OperationsError("service_no_longer_online")
                if (process.status == "online" and process.pid is not None
                        and self._health_ok(service, remaining) and self.monotonic() <= deadline):
                    return
            if self._stop.wait(min(0.25, max(0, deadline - self.monotonic()))):
                break
        raise OperationsError("operations_shutdown")

    def _refuse_external_dependents(self, service, selected):
        for other in self.registry.list_services():
            if other.id not in selected and service.id in getattr(other, "depends_on", ()):
                if self._online(other.id):
                    raise OperationsError("dependency_used_outside_group")

    def _mutate(self, service, action, selected, bound, *, requested_action=None,
                enforce_controls=True, intent_versions=None, automatic=False):
        with self._mutation(service):
            self._check_intent(service, intent_versions)
            if enforce_controls:
                self._require_action(service, requested_action or action)
            guard = getattr(self.pm, "assert_operation_ownership", None)
            if callable(guard):
                guard(service)
            if action == "start":
                self._dependencies_ready(service, bound)
                if not automatic:
                    self.prepare_intent(service, "start")
                    if intent_versions is not None:
                        intent_versions[service.id] = self._action_generations[service.id]
                if not self._online(service.id):
                    if automatic:
                        self._automatic_start_allowed(service)
                        self._reserve_automatic_start(service)
                    self.pm.start(service)
                    self.record_action(service.id, "start", explicit=False)
            else:
                self._refuse_external_dependents(service, selected)
                self.prepare_intent(service, "restart" if requested_action == "restart" else "stop")
                if intent_versions is not None:
                    intent_versions[service.id] = self._action_generations[service.id]
                self.pm.stop(service)
                self.record_action(service.id, "restart" if requested_action == "restart" else "stop", explicit=False)
        if action == "start":
            self._wait_ready(service, intent_versions=intent_versions)

    def submit_group(self, group, action):
        self._available()
        if action not in {"start", "stop", "restart"}:
            raise OperationsError("unsupported_action")
        with _config_reconcile_lock:
            selected = tuple(s for s in self.registry.list_services() if getattr(s, "group", "") == group)
            if not selected:
                raise OperationsError("group_not_found")
            ordered = dependency_order(selected)
        with self._lock:
            if any(job["status"] in {"queued", "running"} for job in self._jobs.values()):
                raise OperationsError("operation_in_progress")
            job = {"id": uuid.uuid4().hex, "status": "queued", "action": action,
                   "group": group, "results": []}
            self._jobs[job["id"]] = job
            self._job_intents[job["id"]] = {
                s.id: self._action_generations.get(s.id, 0) for s in ordered}
            while len(self._jobs) > self.JOB_LIMIT:
                expired_id, _ = self._jobs.popitem(last=False)
                self._job_intents.pop(expired_id, None)
            initial = deepcopy(job)
            self._job_thread = threading.Thread(target=self._run_job, args=(job["id"], ordered),
                                                 name="service-group-operation", daemon=True)
            self._job_thread.start()
            return initial

    def job(self, job_id):
        self._available()
        with self._lock:
            if job_id not in self._jobs:
                raise OperationsError("job_not_found")
            return deepcopy(self._jobs[job_id])

    def _update_job(self, job_id, **fields):
        with self._lock:
            self._jobs[job_id].update(fields)

    def _run_job(self, job_id, ordered):
        self._update_job(job_id, status="running")
        with self._lock:
            action = self._jobs[job_id]["action"]
            intent_versions = self._job_intents[job_id]
        selected = {s.id for s in ordered}
        bound = {s.id: s for s in ordered}
        results = OrderedDict()
        failed = set()
        try:
            # Bind initial online membership for restart; stopped members stay stopped.
            initial_online = set()
            for service in ordered:
                with self._mutation(service):
                    if self._online(service.id):
                        initial_online.add(service.id)
            if action in {"stop", "restart"}:
                for service in reversed(ordered):
                    if action == "restart" and service.id not in initial_online:
                        results[service.id] = {"service_id": service.id, "status": "skipped",
                                               "message": "initially_stopped"}
                        continue
                    # A failed dependent stop must also block stopping its dependencies.
                    if any(sid in failed and service.id in getattr(bound[sid], "depends_on", ())
                           for sid in selected):
                        failed.add(service.id)
                        results[service.id] = {"service_id": service.id, "status": "blocked",
                                               "message": "dependent_stop_failed"}
                        continue
                    try:
                        self._mutate(service, "stop", selected, bound, requested_action=action,
                                     intent_versions=intent_versions)
                        results[service.id] = {"service_id": service.id, "status": "succeeded"}
                    except Exception as exc:  # No command/env/log text in jobs.
                        failed.add(service.id)
                        results[service.id] = {"service_id": service.id, "status": "failed",
                            "message": str(exc) if isinstance(exc, OperationsError) else "operation_failed"}
            if action in {"start", "restart"}:
                for service in ordered:
                    if service.id in failed or (action == "restart" and service.id not in initial_online):
                        continue
                    if any(dep in failed for dep in getattr(service, "depends_on", ())):
                        failed.add(service.id)
                        results[service.id] = {"service_id": service.id, "status": "blocked",
                                               "message": "dependency_failed"}
                        continue
                    try:
                        self._mutate(service, "start", selected, bound, requested_action=action,
                                     intent_versions=intent_versions)
                        results[service.id] = {"service_id": service.id, "status": "succeeded"}
                    except Exception as exc:
                        failed.add(service.id)
                        results[service.id] = {"service_id": service.id, "status": "failed",
                            "message": str(exc) if isinstance(exc, OperationsError) else "operation_failed"}
            self._update_job(job_id, status="failed" if failed else "succeeded", results=list(results.values()))
        except Exception as exc:
            self._update_job(job_id, status="failed", results=list(results.values()),
                error=str(exc) if isinstance(exc, OperationsError) else "operation_failed")

    def autostart(self):
        """Production PM2 startup: never implicitly select autostart:false dependencies."""
        # Migrate stopped membership before selecting any launch candidates.
        with _config_reconcile_lock:
            recovery_snapshot = getattr(self.pm, "recovery_snapshot", None)
            snapshot = recovery_snapshot()[0] if callable(recovery_snapshot) else self.pm.snapshot()
            with self._lock:
                if self._state_error:
                    return
                for service in self.registry.list_services():
                    process = snapshot.get(service.id)
                    if (process is not None and process.status == "stopped"
                            and "desired_state" not in self._history.get(service.id, {})):
                        entry = self._entry(service.id)
                        entry["desired_state"] = "stopped"
                        entry["definition_id"] = _definition_id(service)
                self._persist()
                selected = tuple(s for s in self.registry.list_services()
                                 if s.lifecycle.autostart
                                 and self._history.get(s.id, {}).get("desired_state") != "stopped")
                versions = {s.id: self._action_generations.get(s.id, 0) for s in selected}
        ordered = dependency_order(selected)
        bound = {s.id: s for s in selected}
        failed = set()
        for service in ordered:
            if self._stop.is_set():
                return
            if any(dep in failed for dep in getattr(service, "depends_on", ())):
                failed.add(service.id)
                continue
            for attempt in range(3):
                try:
                    self._mutate(service, "start", set(bound), bound, enforce_controls=False,
                                 intent_versions=versions, automatic=True)
                    break
                except OperationsError as exc:
                    # Startup blocking is diagnostic only; it must not create a
                    # daemon-replacement recovery ticket on the same daemon.
                    if str(exc) != "intervening_manual_action":
                        with self._lock:
                            reason = str(exc)
                            entry = self._entry(service.id)
                            entry["startup_blocked_reason"] = reason if reason in {
                                "previous_process_unconfirmed", "definition_changed", "automatic_recovery_disabled",
                                "automatic_start_budget_exhausted", "dependency_not_ready",
                            } else "readiness_failed"
                            if not self._state_error:
                                self._persist()
                    failed.add(service.id)
                    break
                except Exception:
                    if attempt == 2:
                        failed.add(service.id)
                    elif self._stop.wait(0.25 * 2**attempt):
                        return

    def recovery_status(self, sid):
        with self._lock:
            entry = self._entry(sid)
            unavailable = (hasattr(self.pm, "daemon_identity") and self.pm.daemon_identity() is None)
            return {"desired_state": entry.get("desired_state"),
                    "status": "blocked" if self._state_error or entry.get("startup_blocked_reason") else entry.get("daemon_restore_status"),
                    "reason": self._state_error or entry.get("startup_blocked_reason") or entry.get("daemon_restore_reason") or (
                        "automatic_recovery_disabled" if not self._automatic_enabled else
                        "daemon_identity_unavailable" if unavailable else None)}

    def _automatic_start_allowed(self, service):
        if not self._automatic_enabled:
            raise OperationsError("automatic_recovery_disabled")
        if self._state_error:
            raise OperationsError(self._state_error)
        entry = self._entry(service.id)
        if entry.get("desired_state") == "stopped":
            raise OperationsError("intervening_manual_action")
        if entry.get("definition_id") and entry["definition_id"] != _definition_id(service):
            raise OperationsError("definition_changed")
        conflict = getattr(self.pm, "recovery_process_conflict", None)
        if callable(conflict):
            if service.port is None or conflict(service) is not False:
                raise OperationsError("previous_process_unconfirmed")
        if entry.get("owned_pid") is not None:
            inspect = getattr(self.pm, "runtime_identity_alive", None)
            if not callable(inspect) or inspect(entry["owned_pid"], entry.get("owned_created_at")) is not False:
                raise OperationsError("previous_process_unconfirmed")

    def _reserve_automatic_start(self, service):
        entry = self._entry(service.id)
        identity = getattr(self.pm, "daemon_identity", lambda: None)()
        if identity is not None and identity != entry.get("daemon_id"):
            entry["daemon_id"] = identity
            entry["daemon_restore_attempts"] = 0
            entry.pop("daemon_restore_at", None)
        if (entry.get("daemon_restore_attempts", 0) >= 3
                or self.clock() - entry.get("daemon_restore_at", 0) < 60):
            raise OperationsError("automatic_start_budget_exhausted")
        entry["daemon_restore_attempts"] = entry.get("daemon_restore_attempts", 0) + 1
        entry["daemon_restore_at"] = self.clock()
        entry["desired_state"] = "running"
        entry["definition_id"] = _definition_id(service)
        self._persist()

    def _remember_identity(self, sid, entry):
        getter = getattr(self.pm, "get_state_readonly", None)
        if callable(getter):
            state = getter(sid)
            if state.pid is not None and state.create_time is not None:
                entry["owned_pid"] = state.pid
                entry["owned_created_at"] = state.create_time

    def _restore_after_daemon_change(self, service, process, daemon_id):
        """Use ordinary PM2 starts only after a proven daemon replacement.

        Never restart ordinary crashed/stopped entries or kill an orphan. Existing
        PM2 port/definition checks remain authoritative; failed recovery is visible.
        """
        if daemon_id is None:
            return
        if self._state_error:
            return
        sid = service.id
        with self._mutation(service):
            with self._lock:
                entry = self._entry(sid)
                before = dict(entry)
                old_id = entry.get("daemon_id")
                changed = old_id is not None and old_id != daemon_id
                if changed:
                    entry["daemon_restore_attempts"] = 0
                    if entry.get("desired_state") == "running":
                        entry["daemon_restore_status"] = "pending"
                        entry.pop("daemon_restore_reason", None)
                entry["daemon_id"] = daemon_id
                # Bootstrap only from confirmed PM2 state; absence is not intent.
                if "desired_state" not in entry and process is not None:
                    if process.status in {"online", "launching", "stopped"}:
                        entry["desired_state"] = "running" if process.alive else "stopped"
                        entry["definition_id"] = _definition_id(service)
                if process is not None and process.alive and not changed:
                    self._remember_identity(sid, entry)
                status = entry.get("daemon_restore_status")
                if entry != before:
                    self._persist()
                if (entry.get("desired_state") != "running"
                        or status not in {"pending", "restoring", "blocked", "failed"}):
                    return
                if any(job["status"] in {"queued", "running"} for job in self._jobs.values()):
                    return
                version = {sid: self._action_generations.get(sid, 0)}
                if entry.get("definition_id") != _definition_id(service):
                    if entry.get("daemon_restore_reason") != "definition_changed":
                        entry["daemon_restore_status"] = "blocked"
                        entry["daemon_restore_reason"] = "definition_changed"
                        self._event(sid, "daemon_restore_blocked")
                        self._persist()
                    return
            # Never undo a current stopped/errored PM2 entry. Only an absent app
            # lost with the daemon is eligible; an online restoration is verified.
            fresh = self.pm.get_process(sid, force=True)
            if getattr(self.pm, "daemon_identity", lambda: daemon_id)() != daemon_id:
                return
            if fresh is not None and not fresh.alive:
                with self._lock:
                    if status != "blocked" or entry.get("daemon_restore_reason") != "existing_pm2_entry":
                        entry["daemon_restore_status"] = "blocked"
                        entry["daemon_restore_reason"] = "existing_pm2_entry"
                        self._event(sid, "daemon_restore_blocked")
                        self._persist()
                return
            try:
                self._dependencies_ready(service)
                if fresh is None and service.recovery.auto_takeover:
                    from net_utils import find_port_holder
                    holder = find_port_holder(service.port) if service.port is not None else None
                    if holder is not None:
                        evaluation = self.pm.evaluate_external_identity(service)
                        if not evaluation.diagnostics.ok or evaluation.candidate is None:
                            raise OperationsError("external_takeover_not_allowed")
                        candidate = evaluation.candidate
                        self.take_over_external(service, pid=candidate.pid,
                                                created_at=candidate.create_time,
                                                automatic=True, daemon_id=daemon_id)
                        with self._lock:
                            entry["daemon_restore_status"] = "restored"
                            entry.pop("daemon_restore_reason", None)
                            self._persist()
                        return
                if fresh is None:
                    if (entry.get("daemon_restore_attempts", 0) >= 3
                            or self.clock() - entry.get("daemon_restore_at", 0) < 60):
                        return
                    self._automatic_start_allowed(service)
                    inspect_identity = getattr(self.pm, "runtime_identity_alive", None)
                    if (service.port is None or not callable(inspect_identity)
                            or inspect_identity(entry.get("owned_pid"), entry.get("owned_created_at")) is not False):
                        raise OperationsError("previous_process_unconfirmed")
                    if not callable(getattr(self.pm, "recovery_process_conflict", None)):
                        raise OperationsError("previous_process_unconfirmed")
                    # Check conflicts before consuming a retry. No automatic kill.
                    if service.port is not None:
                        from net_utils import find_port_holder
                        if find_port_holder(service.port) is not None:
                            raise OperationsError("external_port_occupied")
                    with self._lock:
                        entry["daemon_restore_status"] = "restoring"
                        entry.pop("daemon_restore_reason", None)
                        entry["daemon_restore_attempts"] = entry.get("daemon_restore_attempts", 0) + 1
                        entry["daemon_restore_at"] = self.clock()
                        self._event(sid, "daemon_restore_requested")
                        self._persist()  # Fail closed before any mutation.
                    self.pm.start(service)
            except Exception as exc:
                with self._lock:
                    outcome = "blocked" if isinstance(exc, OperationsError) else "failed"
                    reason = str(exc) if isinstance(exc, OperationsError) else "pm2_start_failed"
                    entry["daemon_restore_reason"] = reason if reason in _DAEMON_RESTORE_REASONS else "pm2_start_failed"
                    if entry.get("daemon_restore_status") != outcome:
                        entry["daemon_restore_status"] = outcome
                        self._event(sid, "daemon_restore_" + outcome)
                    self._persist()
                return
        # Release locks during readiness waits; manual stop wins over recovery.
        try:
            self._wait_ready(service, intent_versions=version)
        except Exception:
            outcome = "failed"
        else:
            outcome = "restored"
        with self._lock:
            if (self._action_generations.get(sid, 0) != version[sid]
                    or self.registry.get(sid) != service):
                return
            entry["daemon_restore_status"] = outcome
            if outcome == "restored":
                entry.pop("daemon_restore_reason", None)
            else:
                entry["daemon_restore_reason"] = "readiness_failed"
            if outcome == "restored":
                self._remember_identity(sid, entry)
            self._event(sid, "daemon_restore_succeeded" if outcome == "restored" else "daemon_restore_failed")
            self._persist()

    def _observe(self, service, process):
        sid = service.id
        current = (process.status, process.pid, process.restart_count) if process else ("absent", None, 0)
        with self._lock:
            previous = self._observed.get(sid)
            entry = self._entry(sid)
            baseline = previous[2] if previous is not None else entry["restart_count"]
            changed = previous != current
            if process:
                entry["restart_count"] = process.restart_count
                # PM2 may retain exit_code=0 while running; it is not an exit event.
                if process.exit_code is not None:
                    entry["last_exit_code"] = process.exit_code
            if changed:
                if current[0] == "online" and (previous is None or previous[0] != "online"):
                    self._event(sid, "online_observed", restart_count=current[2])
                elif previous is not None and previous[0] == "online" and current[0] != "online":
                    entry["last_exit_time"] = self.clock()
                    self._event(sid, "stopped_observed", exit_code=entry["last_exit_code"])
                if current[2] > baseline:
                    entry["last_exit_time"] = self.clock()
                    self._event(sid, "restart_observed", restart_count=current[2], exit_code=entry["last_exit_code"])
                self._observed[sid] = current
                self._persist()

    def _recover(self, service, process):
        cfg = getattr(service, "recovery", None)
        sid = service.id
        with self._lock:
            if (not self._automatic_enabled or self._state_error
                    or self._entry(sid).get("desired_state") == "stopped"):
                self._failures.pop(sid, None)
                return
            busy = any(job["status"] in {"queued", "running"} for job in self._jobs.values())
        if busy:
            self._failures.pop(sid, None)
            return
        if (not cfg or not cfg.enabled or not service.health.enabled or not process
                or process.status != "online" or process.pid is None
                or process.started_at is None or self.clock() - process.started_at < cfg.startup_grace_seconds):
            self._failures.pop(sid, None)
            return
        state = self.pm.get_state_readonly(sid)
        if state.pid != process.pid or state.create_time is None:
            self._failures.pop(sid, None)
            return
        identity = (service, process.pid, state.create_time, process.started_at)
        previous, count = self._failures.get(sid, (None, 0))
        count = count if previous == identity else 0
        probe_service = replace(service, health=replace(service.health,
            timeout_seconds=min(service.health.timeout_seconds, self.READY_SECONDS)))
        result = self.health.check(probe_service, include_port_check=False, force_health_probe=True)
        if not result.alive or result.unmanaged or result.health.get("ok") is not False:
            self._failures.pop(sid, None)
            return
        count += 1
        self._failures[sid] = (identity, count)
        if count < cfg.failure_threshold:
            return
        with self._mutation(service):
            with self._lock:
                if self._state_error or self._entry(sid).get("desired_state") == "stopped":
                    self._failures.pop(sid, None)
                    return
            current = self.pm.get_process(sid, force=True)
            state = self.pm.get_state_readonly(sid)
            if (not current or current.status != "online" or current.pid != identity[1]
                    or current.started_at != identity[3] or state.pid != identity[1]
                    or state.create_time != identity[2]):
                self._failures.pop(sid, None)
                return
            result = self.health.check(probe_service, include_port_check=False, force_health_probe=True)
            if not result.alive or result.unmanaged or result.health.get("ok") is not False:
                self._failures.pop(sid, None)
                return
            # Probing may span an independent PM2 restart: verify identity again.
            current = self.pm.get_process(sid, force=True)
            state = self.pm.get_state_readonly(sid)
            if (not current or current.status != "online" or current.pid != identity[1]
                    or current.started_at != identity[3] or state.pid != identity[1]
                    or state.create_time != identity[2]):
                self._failures.pop(sid, None)
                return
            with self._lock:
                entry = self._entry(sid)
                attempts = [t for t in entry["recovery_attempts"] if t > self.clock() - 3600]
                last_attempt = entry["last_recovery_attempt"]
                if (len(attempts) >= cfg.max_restarts_per_hour
                        or last_attempt is not None and self.clock() - last_attempt < cfg.cooldown_seconds):
                    return
                # Schema bounds max_restarts_per_hour to 100.
                entry["recovery_attempts"] = (attempts + [self.clock()])[-100:]
                entry["last_recovery_attempt"] = self.clock()
                self._event(sid, "recovery_requested")
                # Fail closed if the restart budget cannot be durably recorded.
                self._persist()
            try:
                self.pm.restart(service)
            except Exception:
                with self._lock:
                    self._event(sid, "recovery_failed")
                    self._persist()
                return
            finally:
                self._failures.pop(sid, None)
        # Release mutation locks while waiting so explicit stop/config edits win.
        # Every readiness probe reacquires locks and verifies the bound definition.
        try:
            self._wait_ready(service)
        except Exception:
            kind = "recovery_failed"
        else:
            kind = "recovery_succeeded"
        with self._lock:
            self._event(sid, kind)
            self._persist()

    def poll_once(self):
        """One bounded cadence sample, exposed for deterministic tests."""
        with self._poll_lock:
            self._available()
            now = self.monotonic()
            if self._last_poll is not None and now - self._last_poll < self.POLL_SECONDS:
                return
            self._last_poll = now
            recovery_snapshot = getattr(self.pm, "recovery_snapshot", None)
            if callable(recovery_snapshot):
                snapshot, daemon_id = recovery_snapshot()
            else:
                snapshot, daemon_id = self.pm.snapshot(), None
            services = self.registry.list_services()
            for service in dependency_order(services):
                try:
                    self._observe(service, snapshot.get(service.id))
                    self._restore_after_daemon_change(service, snapshot.get(service.id), daemon_id)
                    self._recover(service, snapshot.get(service.id))
                except Exception:
                    self._failures.pop(service.id, None)
                    _log.warning("Service observation/recovery failed (details withheld)")
            rotate = getattr(self.pm, "rotate_running_logs", None)
            if callable(rotate):
                try:
                    with _config_reconcile_lock, ExitStack() as stack:
                        self._available()
                        services = self.registry.list_services()
                        for sid in sorted(s.id for s in services):
                            stack.enter_context(self.pm.service_operation(sid))
                        rotate(services)
                except Exception:
                    _log.warning("Running log rotation failed (details withheld)")
            active = {s.id for s in services}
            self._observed = {sid: v for sid, v in self._observed.items() if sid in active}
            self._failures = {sid: v for sid, v in self._failures.items() if sid in active}

    def start(self):
        if not self.supported or self._stop.is_set():
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="service-observer", daemon=True)
            self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                self._failures.clear()
                _log.warning("PM2 observation unavailable (details withheld)")
            self._stop.wait(self.POLL_SECONDS)

    def shutdown(self):
        self._stop.set()
        for thread in (self._thread, self._job_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=2)
