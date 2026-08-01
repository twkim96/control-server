"""컨트롤 서버 엔트리포인트.

실행 예시:

    CONTROL_PASSWORD=secret .venv/bin/python backend/app.py

* `--config` 옵션으로 다른 config 파일을 가리킬 수 있다.
* 프로세스 추적 일관성을 위해 단일 프로세스로만 실행한다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import psutil
from flask import Flask, jsonify

from action_runner import ActionRunner
from auth import PASSWORD_ENV, init_app as init_auth, is_password_configured
from config_checkpoint import ConfigCheckpoint, ConfigCheckpointError
from config_loader import ConfigError, load_config
from file_browser import FileBrowser
from health_checker import HealthChecker
from log_manager import LogManager
from process_manager import ProcessManager, RuntimeState
from pm2_manager import Pm2Error, Pm2Manager
from resource_sampler import ResourceSampler
from routes import actions as actions_routes
from routes import appearance_api as appearance_api_routes
from routes import api as api_routes
from routes import auth_api as auth_api_routes
from routes import config_api as config_api_routes
from routes import pages as pages_routes
from routes import system as system_routes
from run_log_manager import RunLogManager
from service_registry import ServiceRegistry


BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_CONFIG_PATH = BACKEND_DIR / "config.yml"
DEFAULT_LOG_DIR = BACKEND_DIR / "logs"
DEFAULT_RUNTIME_DIR = BACKEND_DIR / "runtime"
DEFAULT_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


def _read_app_version() -> str:
    try:
        payload = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        version = payload.get("version")
        return version if isinstance(version, str) and version else "unknown"
    except (OSError, ValueError, TypeError):
        return "unknown"


APP_VERSION = _read_app_version()

CONFIG_PATH_ENV = "CONTROL_CONFIG_PATH"
LOG_DIR_ENV = "CONTROL_LOG_DIR"
RUNTIME_DIR_ENV = "CONTROL_RUNTIME_DIR"
FRONTEND_DIST_ENV = "CONTROL_FRONTEND_DIST"


@dataclass(frozen=True)
class AppPaths:
    config_path: Path
    log_dir: Path
    runtime_dir: Path
    frontend_dist: Path


def resolve_app_paths(
    *,
    config_path: str | os.PathLike[str] | None = None,
    log_dir: str | os.PathLike[str] | None = None,
    runtime_dir: str | os.PathLike[str] | None = None,
    frontend_dist: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> AppPaths:
    """Resolve CLI, environment, then checkout-compatible default paths."""

    env = os.environ if environ is None else environ

    def pick(
        explicit: str | os.PathLike[str] | None,
        env_name: str,
        default: Path,
    ) -> Path:
        if explicit is not None:
            return Path(explicit).expanduser()
        from_env = env.get(env_name)
        if from_env:
            return Path(from_env).expanduser()
        return default

    return AppPaths(
        config_path=pick(config_path, CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH),
        log_dir=pick(log_dir, LOG_DIR_ENV, DEFAULT_LOG_DIR),
        runtime_dir=pick(runtime_dir, RUNTIME_DIR_ENV, DEFAULT_RUNTIME_DIR),
        frontend_dist=pick(frontend_dist, FRONTEND_DIST_ENV, DEFAULT_FRONTEND_DIST),
    )


def create_app(
    *,
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG_PATH,
    log_dir: str | os.PathLike[str] = DEFAULT_LOG_DIR,
    runtime_dir: str | os.PathLike[str] = DEFAULT_RUNTIME_DIR,
    frontend_dist: str | os.PathLike[str] = DEFAULT_FRONTEND_DIST,
    run_autostart: bool = False,
    process_backend: str = "native",
    pm2_runner=None,
) -> Flask:
    config_path = Path(config_path)
    checkpoint = ConfigCheckpoint(runtime_dir)
    checkpoint_existed = checkpoint.exists()
    try:
        config = load_config(config_path)
    except ConfigError:
        if not checkpoint.exists():
            raise
        checkpoint.restore_to(config_path)
        config = load_config(config_path)
        logging.getLogger("server_control.config").warning(
            "invalid startup config restored from private last-good checkpoint"
        )

    app = Flask(__name__)
    init_auth(app, secret_key_path=Path(runtime_dir) / ".secret_key")

    log_manager = LogManager(log_dir)
    run_log_manager = RunLogManager(Path(log_dir) / "actions")
    if process_backend == "pm2":
        external_helper = ProcessManager(Path(runtime_dir) / "external", log_manager)
        process_manager = Pm2Manager(
            runtime_dir,
            log_dir,
            runner=pm2_runner,
            external_helper=external_helper,
            log_manager=log_manager,
        )
        issues = process_manager.manifest_definition_issues(config.services)
        if checkpoint_existed:
            checkpoint_config = checkpoint.load()
            if checkpoint_config.raw != config.raw:
                checkpoint.restore_to(config_path)
                config = checkpoint_config
                issues = process_manager.manifest_definition_issues(config.services)
                logging.getLogger("server_control.config").warning(
                    "startup config differed from last-good checkpoint and was restored"
                )
        if issues:
            # First v1.4.2 boot naturally sees legacy manifests. Keep the
            # current/checkpoint-approved YAML, surface configured entries as
            # restart-required, and clean only stopped orphans in background.
            process_manager.record_startup_manifest_issues(config.services, issues)
            logging.getLogger("server_control.config").warning(
                "PM2 manifest reconciliation required: %s",
                ", ".join(sorted(issues)),
            )
    elif process_backend == "native":
        process_manager = ProcessManager(runtime_dir, log_manager)
    else:
        raise ConfigError(f"unsupported process backend: {process_backend}")
    try:
        checkpoint.save_from(config_path)
    except ConfigCheckpointError as exc:
        raise ConfigError(str(exc)) from exc
    registry = ServiceRegistry(config)
    for service in config.services:
        process_manager.activate_service(service)
    health_checker = HealthChecker(process_manager, health_ttl_seconds=2.0)
    file_browser = FileBrowser(config.controller.allowed_path_roots)
    action_runner = ActionRunner(run_log_manager)
    resource_sampler = ResourceSampler(ttl_seconds=10.0)
    controller_process = psutil.Process(os.getpid())
    controller_runtime_state = RuntimeState(
        pid=controller_process.pid,
        create_time=controller_process.create_time(),
    )

    app.config["config_path"] = str(config_path)
    app.config["config_checkpoint"] = checkpoint
    app.config["registry"] = registry
    app.config["log_manager"] = log_manager
    app.config["run_log_manager"] = run_log_manager
    app.config["process_manager"] = process_manager
    app.config["process_backend"] = process_backend
    app.config["health_checker"] = health_checker
    app.config["file_browser"] = file_browser
    app.config["action_runner"] = action_runner
    app.config["resource_sampler"] = resource_sampler
    app.config["controller_runtime_state"] = controller_runtime_state
    app.config["frontend_dist"] = str(frontend_dist)
    app.config["appearance_store_path"] = str(Path(runtime_dir) / "appearance.json")

    app.register_blueprint(auth_api_routes.bp)
    app.register_blueprint(api_routes.bp)
    app.register_blueprint(config_api_routes.bp)
    app.register_blueprint(actions_routes.bp)
    app.register_blueprint(appearance_api_routes.bp)
    app.register_blueprint(system_routes.bp)
    app.register_blueprint(pages_routes.bp)

    @app.errorhandler(Pm2Error)
    def handle_pm2_error(exc: Pm2Error):
        return jsonify({"error": "pm2_unavailable", "message": str(exc)}), 503

    @app.get("/api/meta")
    def meta():
        return jsonify(
            {
                "ok": True,
                "version": APP_VERSION,
                "password_configured": is_password_configured(),
                "controller": {
                    "host": config.controller.host,
                    "port": config.controller.port,
                },
                "service_count": len(config.services),
            }
        )

    if run_autostart:
        lifecycle_thread = threading.Thread(
            target=_run_startup_lifecycle,
            args=(registry, process_manager),
            name="control-startup-lifecycle",
            daemon=True,
        )
        lifecycle_thread.start()
        app.config["startup_lifecycle_thread"] = lifecycle_thread

    return app


def _run_startup_lifecycle(
    registry: ServiceRegistry,
    process_manager: ProcessManager,
) -> None:
    """Run service reconciliation without delaying the Control Server listener."""

    log = logging.getLogger("server_control.startup")
    try:
        reconcile_orphans = getattr(process_manager, "reconcile_startup_orphans", None)
        if callable(reconcile_orphans):
            try:
                reconcile_orphans()
            except Exception as exc:  # noqa: BLE001
                log.warning("background PM2 orphan reconciliation failed: %s", exc)
        if getattr(process_manager, "supports_adoption", True):
            _run_adopt_pass(registry, process_manager)
        _run_autostart(registry, process_manager)
    except Exception as exc:  # noqa: BLE001
        log.warning("background startup lifecycle failed: %s", exc)


def _run_adopt_pass(registry: ServiceRegistry, process_manager: ProcessManager) -> None:
    """lifecycle.unmanaged_policy=manage 서비스를 시작 시 한 번 입양 시도한다 (v1.2.5).

    컨트롤 서버가 어떤 이유로 자식을 잃은 채 떠도, 외부에 떠있는 자식이 config 모양과
    일치하면 자동으로 추적을 다시 잡는다.
    """
    log = logging.getLogger("server_control.adopt")
    adopted = process_manager.try_adopt_all(registry.list_services())
    for sid in adopted:
        log.info("adopted external instance: %s", sid)


def _run_autostart(registry: ServiceRegistry, process_manager: ProcessManager) -> None:
    """lifecycle.autostart=True 서비스를 컨트롤 서버 시작 시 자동으로 띄운다.

    이미 살아있는 서비스(예: 컨트롤 서버 재시작 후 PID 추적이 복구된 경우)는 건너뛴다.
    한 서비스가 실패해도 다른 서비스는 계속 시도한다.
    """
    log = logging.getLogger("server_control.autostart")
    for service in registry.list_services():
        if not service.lifecycle.autostart:
            continue
        confirmed_is_alive = getattr(
            process_manager,
            "is_alive_confirmed",
            process_manager.is_alive,
        )
        for attempt in range(3):
            try:
                if confirmed_is_alive(service.id):
                    log.info("autostart skip (already alive): %s", service.id)
                    break
                state = process_manager.start(service)
                log.info("autostart ok: %s pid=%s", service.id, state.pid)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    log.warning("autostart failed after retries: %s -> %s", service.id, exc)
                    break
                delay = 0.25 * (2**attempt)
                log.warning(
                    "autostart retry: %s attempt=%s delay=%.2fs -> %s",
                    service.id,
                    attempt + 1,
                    delay,
                    exc,
                )
                time.sleep(delay)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="server_control backend")
    parser.add_argument("--config", default=None)
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--frontend-dist", default=None)
    parser.add_argument("--host", default=None, help="config의 controller.host를 override")
    parser.add_argument("--port", type=int, default=None, help="config의 controller.port를 override")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--server",
        choices=["flask", "waitress"],
        default="waitress",
        help="기본은 waitress 단일 프로세스",
    )
    args = parser.parse_args(argv)
    paths = resolve_app_paths(
        config_path=args.config,
        log_dir=args.log_dir,
        runtime_dir=args.runtime_dir,
        frontend_dist=args.frontend_dist,
    )

    _setup_logging(args.log_level)

    try:
        app = create_app(
            config_path=paths.config_path,
            log_dir=paths.log_dir,
            runtime_dir=paths.runtime_dir,
            frontend_dist=paths.frontend_dist,
            run_autostart=True,
            process_backend=os.environ.get("CONTROL_PROCESS_BACKEND", "native"),
        )
    except ConfigError as exc:
        print(f"[config error] {exc}", file=sys.stderr)
        return 2

    cfg = load_config(paths.config_path)
    host = args.host or cfg.controller.host
    port = args.port or cfg.controller.port

    if not is_password_configured():
        logging.warning(
            "%s 환경변수가 설정되지 않았습니다. 모든 API는 503으로 거부됩니다.",
            PASSWORD_ENV,
        )

    if args.server == "flask":
        app.run(host=host, port=port, threaded=True, use_reloader=False)
    else:
        from waitress import serve

        # SSE 연결이 long-lived이라 thread를 오래 잡는다. 여유를 둔다.
        serve(app, host=host, port=port, threads=32, channel_timeout=60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
