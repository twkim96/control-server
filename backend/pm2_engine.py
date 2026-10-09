"""Private PM2 engine jobs; the detached updater survives dashboard reloads."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psutil


class EngineError(RuntimeError):
    pass


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Pm2Engine:
    def __init__(self, runtime_dir, app_root, config_path, *, enabled: bool):
        self.runtime = Path(runtime_dir).resolve()
        self.root = self.runtime / "pm2-engine"
        self.app_root = Path(app_root).resolve()
        self.config_path = Path(config_path).resolve()
        self.enabled = enabled
        self.node = os.environ.get("CONTROL_PM2_NODE") or shutil.which("node")
        self._lock = threading.Lock()
        self._child: subprocess.Popen | None = None
        self._pending: dict | None = None
        self._pending_previous_id: str | None = None

    def _arguments(self, operation: str, *extra: str):
        return [self.node, str(self.app_root / "lib/pm2-engine.mjs"), operation,
                str(self.runtime), str(self.app_root), str(self.config_path), *extra]

    def _supported(self):
        return bool(self.enabled and self.node and Path(self.node).is_file()
                    and (self.app_root / "lib/pm2-engine.mjs").is_file())

    def status(self):
        if self._child is not None:
            self._child.poll()  # Reap completed updater children without blocking.
        cli = self.root / "current/node_modules/pm2/bin/pm2"
        if not cli.is_file():
            cli = Path(os.environ.get("CONTROL_PM2_CLI") or
                       self.app_root / "ops/pm2/node_modules/pm2/bin/pm2")
        package = _read_json(cli.parent.parent / "package.json") or {}
        current = package.get("version")
        daemon = None
        try:
            pid = int((self.runtime / "pm2/pm2.pid").read_text().strip())
            title = " ".join(psutil.Process(pid).cmdline())
            match = re.search(r"PM2 v(\d+\.\d+\.\d+): God Daemon", title)
            if match and str(self.runtime / "pm2") in title:
                daemon = match.group(1)
        except (OSError, ValueError, psutil.Error):
            pass
        latest = _read_json(self.root / "latest.json") or {}
        job = _read_json(self.root / "job.json")
        if self._pending and self._child is not None:
            if isinstance(job, dict) and job.get("id") != self._pending_previous_id:
                self._pending = None  # This updater, or a later CLI job, published status.
            else:
                # Keep the submitted job visible even if Node exits before its first write.
                job = {**self._pending, "pid": self._child.pid}
        if isinstance(job, dict):
            owned_running = self._child is not None and self._child.pid == job.get("pid") and self._child.poll() is None
            if job.get("status") == "running" and not owned_running and not self._job_alive(job):
                job = {**job, "status": "failed", "phase": "complete",
                       "message": "업데이트가 중단됐습니다. 업데이트 버튼으로 복구를 다시 시도하세요."}
            job = {key: value for key, value in job.items() if key not in {"pid"}}
        else:
            job = None
        needs_recovery = (self.root / "recovery.json").exists()
        return {
            "supported": self._supported(),
            "reason": None if self._supported() else "이 설치에서는 PM2 엔진 업데이트를 사용할 수 없습니다.",
            "current_version": current, "daemon_version": daemon,
            "latest_version": latest.get("version"), "checked_at": latest.get("checked_at"),
            "update_available": bool(needs_recovery or
                                     (latest.get("version") and latest["version"] != current) or
                                     (daemon and current and daemon != current)),
            "job": job,
        }

    @staticmethod
    def _job_alive(job):
        try:
            process = psutil.Process(int(job["pid"]))
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE and any(
                "pm2-engine.mjs" in arg or "control-server.mjs" in arg for arg in process.cmdline()
            )
        except (KeyError, TypeError, ValueError, psutil.Error):
            return False

    def check(self):
        if not self._supported():
            raise EngineError("이 설치에서는 PM2 엔진 업데이트를 사용할 수 없습니다.")
        try:
            result = subprocess.run(self._arguments("check"), capture_output=True,
                                    timeout=12, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EngineError("최신 PM2 버전을 확인하지 못했습니다. 잠시 후 다시 시도하세요.") from exc
        if result.returncode:
            raise EngineError("최신 PM2 버전을 확인하지 못했습니다. 잠시 후 다시 시도하세요.")
        return self.status()

    def start(self):
        with self._lock:
            if not self._supported():
                raise EngineError("이 설치에서는 PM2 엔진 업데이트를 사용할 수 없습니다.")
            job = self.status().get("job")
            if (self._child is not None and self._child.poll() is None) or (job and job["status"] == "running"):
                raise EngineError("PM2 업데이트가 이미 진행 중입니다.")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.root.chmod(0o700)
            job_id = uuid.uuid4().hex
            previous = _read_json(self.root / "job.json")
            self._pending_previous_id = previous.get("id") if isinstance(previous, dict) else None
            try:
                child = subprocess.Popen(self._arguments("update", job_id),
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            except OSError as exc:
                raise EngineError("PM2 업데이트를 시작하지 못했습니다.") from exc
            self._child = child
            # The updater owns persisted status. An immediate response can precede its first write.
            status = self.status()
            self._pending = {"id": job_id, "status": "running", "phase": "checking",
                             "message": "업데이트를 준비하고 있습니다.",
                             "started_at": datetime.now(timezone.utc).isoformat()}
            status["job"] = self._pending
            return status


def _health_targets(config_path):
    from config_loader import load_config
    from net_utils import parse_tcp_target
    targets = []
    for service in load_config(config_path).services:
        if service.health.enabled:
            host, port = parse_tcp_target(service.health.url) if service.health.type == "tcp" else (None, service.port)
            targets.append({"name": f"server-control--{service.id}", "type": service.health.type,
                            "url": service.health.url, "port": port, "host": host,
                            "timeout_seconds": service.health.timeout_seconds,
                            "verify_ssl": service.health.verify_ssl})
        elif service.port:
            targets.append({"name": f"server-control--{service.id}", "type": "tcp", "port": service.port})
    return targets


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "health-targets":
        print(json.dumps(_health_targets(sys.argv[2])))
    else:
        raise SystemExit(2)
