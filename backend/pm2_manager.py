"""PM2 adapter for long-running managed services.

The YAML config remains the declaration source. PM2 is only the runtime source.
This module never uses the user's default ~/.pm2 instance.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import psutil

from config_loader import ServiceConfig, StopStrategy
from process_manager import AdoptEvaluation, ProcessError, ProcessManager, RuntimeState
from net_utils import find_port_holder


class Pm2Error(ProcessError):
    """Expected PM2 command, state, or configuration failure."""


@dataclass(frozen=True)
class Pm2Process:
    service_id: str
    name: str
    status: str
    pid: int | None
    started_at: float | None
    restart_count: int
    exit_code: int | None

    @property
    def alive(self) -> bool:
        return self.status in {"online", "launching"} and self.pid is not None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], dict[str, str], float], CommandResult]

_NAME_PREFIX = "server-control--"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_log = logging.getLogger("server_control.pm2")


class Pm2Manager:
    def __init__(
        self,
        runtime_dir: str | os.PathLike[str],
        log_dir: str | os.PathLike[str],
        *,
        wrapper: str | os.PathLike[str] | None = None,
        runner: Runner | None = None,
        snapshot_ttl_seconds: float = 10.0,
        command_timeout_seconds: float = 15.0,
        snapshot_retry_seconds: float = 15.0,
        slow_refresh_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        external_helper: ProcessManager | None = None,
    ) -> None:
        backend_dir = Path(__file__).resolve().parent
        repo_root = backend_dir.parent
        self._runtime_root = Path(runtime_dir) / "pm2"
        # macOS sockaddr_un.sun_path is 104 bytes including the terminator.
        # PM2 appends rpc.sock/pub.sock/interactor.sock, so fail before its CLI hangs.
        longest_socket = self._runtime_root / "interactor.sock"
        if runner is None and len(os.fsencode(longest_socket)) >= 104:
            raise Pm2Error(f"PM2_HOME path is too long for macOS sockets: {self._runtime_root}")
        self._manifest_root = self._runtime_root / "manifests"
        self._log_root = Path(log_dir)
        self._wrapper = Path(wrapper or repo_root / "scripts" / "pm2ctl.sh")
        self._runner = runner or self._default_runner
        self._ttl = max(0.0, snapshot_ttl_seconds)
        self._timeout = max(0.1, command_timeout_seconds)
        self._snapshot_retry = max(0.1, snapshot_retry_seconds)
        self._slow_refresh = max(0.1, slow_refresh_seconds)
        self._clock = clock
        self._external_helper = external_helper

        self._condition = threading.Condition()
        self._snapshot: dict[str, Pm2Process] = {}
        self._snapshot_at = 0.0
        self._snapshot_invalidated = False
        self._snapshot_loading = False
        self._snapshot_refresh_started_at = 0.0
        self._snapshot_last_attempt_at = 0.0
        self._snapshot_last_error: str | None = None
        self._closed = False
        self._op_locks: dict[str, threading.RLock] = {}
        self.supports_adoption = False

    @staticmethod
    def app_name(service_id: str) -> str:
        if not _SAFE_ID.fullmatch(service_id):
            raise Pm2Error(f"invalid service id for PM2: {service_id!r}")
        return f"{_NAME_PREFIX}{service_id}"

    @staticmethod
    def service_id_from_name(name: str) -> str | None:
        if not name.startswith(_NAME_PREFIX):
            return None
        service_id = name[len(_NAME_PREFIX) :]
        return service_id if _SAFE_ID.fullmatch(service_id) else None

    def _op_lock(self, service_id: str) -> threading.RLock:
        with self._condition:
            return self._op_locks.setdefault(service_id, threading.RLock())

    @contextmanager
    def service_operation(self, service_id: str):
        with self._op_lock(service_id):
            yield

    def activate_service(self, service: ServiceConfig) -> None:
        self.app_name(service.id)

    def reconcile_service_definitions(self, services: tuple[ServiceConfig, ...]) -> None:
        for service in services:
            self.activate_service(service)

    def build_manifest(self, service: ServiceConfig) -> dict[str, object]:
        cwd = Path(service.cwd)
        if not cwd.is_dir():
            raise Pm2Error(f"working directory does not exist: {cwd}")
        if not service.command:
            raise Pm2Error(f"{service.id}: empty command")

        env = dict(service.env)
        if service.port_env_name and service.port is not None:
            env.setdefault(service.port_env_name, str(service.port))
        if service.https.enabled:
            env.setdefault(service.https.enabled_env_name, "1")
            if service.https.cert_file:
                env.setdefault(
                    service.https.cert_file_env_name,
                    str(_resolve_child_path(cwd, service.https.cert_file)),
                )
            if service.https.key_file:
                env.setdefault(
                    service.https.key_file_env_name,
                    str(_resolve_child_path(cwd, service.https.key_file)),
                )
        env.setdefault("PYTHONUNBUFFERED", "1")

        stop_timeout = 5.0
        for action in service.actions:
            if action.type == "process_stop" and action.stop_strategy is not None:
                stop_timeout = action.stop_strategy.timeout_seconds
                break

        app = {
            "name": self.app_name(service.id),
            "script": service.command[0],
            "args": list(service.command[1:]),
            "interpreter": "none",
            "cwd": str(cwd),
            "env": env,
            "exec_mode": "fork",
            "instances": 1,
            "watch": False,
            "autorestart": True,
            "min_uptime": "5s",
            "max_restarts": 5,
            "restart_delay": 1000,
            "exp_backoff_restart_delay": 100,
            "kill_timeout": max(100, int(stop_timeout * 1000)),
            "log_file": str(self._log_root / f"{service.id}.log"),
            "merge_logs": True,
            "time": False,
        }
        return {"apps": [app]}

    def write_manifest(self, service: ServiceConfig) -> Path:
        manifest = self.build_manifest(service)
        self._manifest_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._runtime_root, 0o700)
        os.chmod(self._manifest_root, 0o700)
        path = self._manifest_root / f"{service.id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return path

    def snapshot(
        self,
        *,
        force: bool = False,
        nonblocking: bool = False,
    ) -> dict[str, Pm2Process]:
        with self._condition:
            now = self._clock()
            if (
                not force
                and self._snapshot_at
                and not self._snapshot_invalidated
                and now - self._snapshot_at < self._ttl
            ):
                return dict(self._snapshot)

            # Once one valid snapshot exists, read-only API traffic never waits
            # for the PM2 CLI. One daemon thread refreshes it while all readers
            # keep receiving the last known-good state. Mutations pass force=True
            # and still require a confirmed PM2 response.
            if nonblocking and not force:
                self._start_background_refresh_locked(now)
                return dict(self._snapshot)

            while self._snapshot_loading:
                self._condition.wait(timeout=self._timeout)
                now = self._clock()
                if (
                    self._snapshot_at
                    and not self._snapshot_invalidated
                    and now - self._snapshot_at < self._ttl
                ):
                    return dict(self._snapshot)
                if not self._snapshot_loading and not self._snapshot_at and self._snapshot_last_error:
                    raise Pm2Error(self._snapshot_last_error)
            self._snapshot_loading = True
            self._snapshot_refresh_started_at = now
            self._snapshot_last_attempt_at = now

        try:
            fresh = self._fetch_snapshot()
        except Exception as exc:
            self._finish_snapshot_refresh(error=exc)
            raise

        self._finish_snapshot_refresh(fresh=fresh)
        return dict(fresh)

    def snapshot_diagnostics(self) -> dict[str, object]:
        """Safe PM2 read health for the API; never includes PM2 stdout/env."""

        with self._condition:
            now = self._clock()
            age = max(0.0, now - self._snapshot_at) if self._snapshot_at else None
            refresh_seconds = (
                max(0.0, now - self._snapshot_refresh_started_at)
                if self._snapshot_loading and self._snapshot_refresh_started_at
                else None
            )
            degraded = bool(
                self._snapshot_last_error is not None
                or (refresh_seconds is not None and refresh_seconds >= self._slow_refresh)
            )
            return {
                "backend": "pm2",
                "degraded": degraded,
                "refreshing": self._snapshot_loading,
                "snapshot_age_seconds": round(age, 3) if age is not None else None,
                "last_error": self._snapshot_last_error,
            }

    def _start_background_refresh_locked(self, now: float) -> None:
        if self._closed or self._snapshot_loading:
            return
        if self._snapshot_last_attempt_at and now - self._snapshot_last_attempt_at < self._snapshot_retry:
            return
        self._snapshot_loading = True
        self._snapshot_refresh_started_at = now
        self._snapshot_last_attempt_at = now
        thread = threading.Thread(
            target=self._refresh_snapshot_background,
            name="pm2-snapshot-refresh",
            daemon=True,
        )
        thread.start()

    def _refresh_snapshot_background(self) -> None:
        try:
            fresh = self._fetch_snapshot()
        except Exception as exc:
            self._finish_snapshot_refresh(error=exc)
            _log.warning("PM2 snapshot refresh failed; serving last known-good state: %s", exc)
            return
        self._finish_snapshot_refresh(fresh=fresh)

    def _finish_snapshot_refresh(
        self,
        *,
        fresh: dict[str, Pm2Process] | None = None,
        error: Exception | None = None,
    ) -> None:
        with self._condition:
            if error is None and fresh is not None:
                self._snapshot = fresh
                self._snapshot_at = self._clock()
                self._snapshot_invalidated = False
                self._snapshot_last_error = None
            elif error is not None:
                # Pm2Error messages are deliberately command-only and never
                # include raw stdout/stderr or process environments.
                self._snapshot_last_error = str(error)
            self._snapshot_loading = False
            self._snapshot_refresh_started_at = 0.0
            self._condition.notify_all()

    def invalidate(self) -> None:
        with self._condition:
            self._snapshot_invalidated = True
            # Explicit state/config mutations should be refreshable immediately.
            # The retry throttle still applies after a background refresh fails.
            self._snapshot_last_attempt_at = 0.0

    def get_process(self, service_id: str, *, force: bool = False) -> Pm2Process | None:
        return self.snapshot(force=force).get(service_id)

    def get_state(self, service_id: str, *, nonblocking: bool = False) -> RuntimeState:
        process = self.snapshot(nonblocking=nonblocking).get(service_id)
        if process is None:
            return RuntimeState()
        pid = process.pid if process.alive else None
        pgid = None
        create_time = None
        if pid is not None:
            try:
                proc = psutil.Process(pid)
                create_time = proc.create_time()
            except psutil.NoSuchProcess:
                pid = None
            except (psutil.AccessDenied, OSError):
                # PM2 is the runtime source. Transient inspection failure is not
                # proof that its online PID died (same conservative rule as v1.3.4).
                pass
            if pid is not None:
                try:
                    pgid = os.getpgid(pid)
                except OSError:
                    pass
        return RuntimeState(
            pid=pid,
            pgid=pgid,
            start_time=process.started_at if pid is not None else None,
            create_time=create_time,
            last_exit_code=process.exit_code,
            last_exit_time=None,
            last_action=None,
            last_action_time=None,
            adopted=False,
            extra={"pm2_restart_count": process.restart_count, "pm2_status": process.status},
        )

    def inspect_state(self, service_id: str) -> tuple[RuntimeState, bool]:
        state = self.get_state(service_id)
        return state, state.pid is not None

    def get_state_readonly(self, service_id: str) -> RuntimeState:
        return self.get_state(service_id, nonblocking=True)

    def inspect_state_readonly(self, service_id: str) -> tuple[RuntimeState, bool]:
        state = self.get_state_readonly(service_id)
        return state, state.pid is not None

    def is_alive(self, service_id: str) -> bool:
        return self.inspect_state(service_id)[1]

    def is_alive_confirmed(self, service_id: str) -> bool:
        process = self.get_process(service_id, force=True)
        return process is not None and process.alive

    def start(self, service: ServiceConfig) -> Pm2Process:
        with self._op_lock(service.id):
            current = self.get_process(service.id, force=True)
            if current is not None and current.alive:
                raise Pm2Error(f"{service.id} is already running (pid={current.pid})")
            if service.port is not None:
                holder = find_port_holder(service.port)
                if holder is not None:
                    raise Pm2Error(
                        f"{service.id}: port {service.port} is already in use "
                        f"(pid={holder['pid']}, name={holder['name']})"
                    )
            path = self.write_manifest(service)
            command_error: Pm2Error | None = None
            try:
                self._command(["start", str(path), "--only", self.app_name(service.id)])
            except Pm2Error as exc:
                # PM2 may accept the start and then lose/timeout the CLI reply.
                # Reconcile against PM2 before reporting failure so callers do
                # not receive a false 409 while the service is already online.
                command_error = exc
            self.invalidate()
            state_error: Pm2Error | None = None
            for attempt in range(3):
                try:
                    state = self.get_process(service.id, force=True)
                    if state is not None and state.alive:
                        return state
                    state_error = None
                except Pm2Error as exc:
                    state_error = exc
                if attempt < 2:
                    time.sleep(0.1)
            if command_error is not None:
                raise command_error
            if state_error is not None:
                raise Pm2Error(f"{service.id}: PM2 state check failed after start") from state_error
            raise Pm2Error(f"{service.id}: PM2 did not report an online process")

    def stop(
        self,
        service: str | ServiceConfig,
        strategy: StopStrategy | None = None,
    ) -> RuntimeState:
        service_id = service if isinstance(service, str) else service.id
        with self._op_lock(service_id):
            state = self.get_process(service_id, force=True)
            if state is None:
                return RuntimeState()
            self._command(["stop", self.app_name(service_id)])
            self.invalidate()
            self.get_process(service_id, force=True)
            return self.get_state(service_id)

    def restart(
        self,
        service: ServiceConfig,
        strategy: StopStrategy | None = None,
    ) -> Pm2Process:
        with self._op_lock(service.id):
            path = self.write_manifest(service)
            self._command(["startOrRestart", str(path), "--only", self.app_name(service.id)])
            self.invalidate()
            state = self.get_process(service.id, force=True)
            if state is None or not state.alive:
                raise Pm2Error(f"{service.id}: PM2 restart did not become online")
            return state

    def delete(self, service_id: str) -> None:
        with self._op_lock(service_id):
            if self.get_process(service_id, force=True) is not None:
                self._command(["delete", self.app_name(service_id)])
            self.invalidate()
            path = self._manifest_root / f"{service_id}.json"
            path.unlink(missing_ok=True)

    def forget_service(self, service_id: str) -> None:
        state, alive = self.inspect_state(service_id)
        if alive:
            raise Pm2Error(f"{service_id} is running (pid={state.pid}); stop it first")
        self.delete(service_id)

    def evaluate_adopt(
        self,
        service: ServiceConfig,
        *,
        health_ok: bool | None = None,
    ) -> AdoptEvaluation:
        if self._external_helper is None:
            raise Pm2Error("external process helper is not configured")
        evaluation = self._external_helper.evaluate_adopt(service, health_ok=health_ok)
        return replace(
            evaluation,
            diagnostics=replace(
                evaluation.diagnostics,
                ok=False,
                reason="pm2_exclusive",
            ),
        )

    def try_adopt(
        self,
        service: ServiceConfig,
        *,
        evaluation: AdoptEvaluation | None = None,
        health_ok: bool | None = None,
    ) -> None:
        return None

    def try_adopt_all(self, services) -> list[str]:
        return []

    def kill_external(self, service: ServiceConfig, **kwargs):
        if self._external_helper is None:
            raise Pm2Error("external process helper is not configured")
        managed = self.get_process(service.id, force=True)
        if managed is not None and managed.alive:
            raise Pm2Error(
                f"{service.id} is managed by PM2 (pid={managed.pid}); external kill refused"
            )
        return self._external_helper.kill_external(service, **kwargs)

    def shutdown(self) -> None:
        with self._condition:
            self._closed = True
            self._snapshot_invalidated = True

    def _fetch_snapshot(self) -> dict[str, Pm2Process]:
        result = self._command(["jlist"])
        raw = _extract_json_array(result.stdout)
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise Pm2Error("PM2 returned an invalid process list") from exc
        if not isinstance(items, list):
            raise Pm2Error("PM2 process list is not an array")

        snapshot: dict[str, Pm2Process] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str):
                continue
            service_id = self.service_id_from_name(name)
            if service_id is None:
                continue
            env = item.get("pm2_env") if isinstance(item.get("pm2_env"), dict) else {}
            pid = item.get("pid") if isinstance(item.get("pid"), int) and item.get("pid") > 0 else None
            uptime_ms = env.get("pm_uptime")
            snapshot[service_id] = Pm2Process(
                service_id=service_id,
                name=name,
                status=str(env.get("status") or "unknown"),
                pid=pid,
                started_at=(float(uptime_ms) / 1000.0 if isinstance(uptime_ms, (int, float)) else None),
                restart_count=int(env.get("restart_time") or 0),
                exit_code=(int(env["exit_code"]) if isinstance(env.get("exit_code"), int) else None),
            )
        return snapshot

    def _command(self, args: list[str]) -> CommandResult:
        env = os.environ.copy()
        env["CONTROL_PM2_HOME"] = str(self._runtime_root)
        started_at = time.monotonic()
        try:
            result = self._runner([str(self._wrapper), *args], env, self._timeout)
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started_at
            _log.warning(
                "PM2 command timed out: command=%s elapsed=%.3fs timeout=%.3fs",
                args[0],
                elapsed,
                self._timeout,
            )
            raise Pm2Error(f"PM2 command timed out: {args[0]}") from exc
        except OSError as exc:
            elapsed = time.monotonic() - started_at
            _log.warning(
                "PM2 command could not start: command=%s elapsed=%.3fs error_type=%s",
                args[0],
                elapsed,
                type(exc).__name__,
            )
            raise Pm2Error(f"PM2 command could not start: {args[0]}") from exc
        elapsed = time.monotonic() - started_at
        if result.returncode != 0:
            _log.warning(
                "PM2 command failed: command=%s elapsed=%.3fs returncode=%s stderr_bytes=%s",
                args[0],
                elapsed,
                result.returncode,
                len(result.stderr.encode("utf-8", errors="replace")),
            )
            raise Pm2Error(f"PM2 command failed: {args[0]}")
        if elapsed >= 1.0:
            _log.info("PM2 command slow: command=%s elapsed=%.3fs", args[0], elapsed)
        return result

    @staticmethod
    def _default_runner(argv: list[str], env: dict[str, str], timeout: float) -> CommandResult:
        result = subprocess.run(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(result.returncode, result.stdout, result.stderr)


def _extract_json_array(output: str) -> str:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if candidate == "[]" or candidate.startswith("[{" ):
            return candidate
    raise Pm2Error("PM2 process list did not contain JSON")


def _resolve_child_path(cwd: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else cwd / path


__all__ = ["CommandResult", "Pm2Error", "Pm2Manager", "Pm2Process"]
