"""autostart 서비스가 컨트롤 서버 시작 시 자동으로 띄워지는지 검증."""
from __future__ import annotations

import time
from pathlib import Path
from textwrap import dedent

import psutil

import app as backend_app
from auth import PASSWORD_ENV


def _seed_config(path: Path, *, autostart: bool, child_script: Path) -> None:
    path.write_text(
        dedent(
            f"""
            controller:
              host: "127.0.0.1"
              port: 9000
              allowed_path_roots:
                - "{child_script.parent}"
            services:
              - id: "auto"
                name: "Auto"
                cwd: "{child_script.parent}"
                entry_file: "{child_script.name}"
                command: ["python", "-u", "{child_script.name}"]
                lifecycle:
                  mode: "always_on"
                  autostart: {"true" if autostart else "false"}
                  stop_visibility: "danger_menu"
                  restart_visibility: "primary"
                  unmanaged_policy: "status_only"
                actions:
                  - id: "stop"
                    label: "stop"
                    type: "process_stop"
                    enabled: true
                    strategy:
                      signal: "SIGINT"
                      timeout_seconds: 3
                      confirm_required: false
                      fallback: ["SIGTERM", "SIGKILL"]
            """
        ).strip(),
        encoding="utf-8",
    )


def _make_child_script(tmp_path: Path) -> Path:
    script = tmp_path / "child.py"
    script.write_text(
        "import signal, time\n"
        "running = True\n"
        "def handler(signum, frame):\n"
        "    global running\n"
        "    running = False\n"
        "signal.signal(signal.SIGINT, handler)\n"
        "signal.signal(signal.SIGTERM, handler)\n"
        "while running:\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    return script


def test_autostart_true_starts_service(tmp_path, monkeypatch):
    monkeypatch.setenv(PASSWORD_ENV, "secret")
    script = _make_child_script(tmp_path)
    config = tmp_path / "config.yml"
    _seed_config(config, autostart=True, child_script=script)

    runtime_dir = tmp_path / "runtime"
    log_dir = tmp_path / "logs"

    app = backend_app.create_app(
        config_path=config,
        log_dir=log_dir,
        runtime_dir=runtime_dir,
        frontend_dist=tmp_path / "_no_dist",
        run_autostart=True,
    )
    pm = app.config["process_manager"]

    try:
        # autostart 직후엔 잠깐 기다려서 spawn이 끝났는지 확인
        time.sleep(0.5)
        assert pm.is_alive("auto")
        state = pm.get_state("auto")
        pid = state.pid
        assert pid is not None
        # 진짜로 살아있는 process인지
        assert psutil.pid_exists(pid)
    finally:
        from config_loader import load_config
        cfg = load_config(config)
        svc = next(s for s in cfg.services if s.id == "auto")
        stop_action = next(a for a in svc.actions if a.id == "stop")
        assert stop_action.stop_strategy is not None
        try:
            pm.stop(svc, stop_action.stop_strategy)
        except Exception:
            pass


def test_autostart_false_does_not_start_service(tmp_path, monkeypatch):
    monkeypatch.setenv(PASSWORD_ENV, "secret")
    script = _make_child_script(tmp_path)
    config = tmp_path / "config.yml"
    _seed_config(config, autostart=False, child_script=script)

    app = backend_app.create_app(
        config_path=config,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "_no_dist",
        run_autostart=True,
    )
    pm = app.config["process_manager"]

    time.sleep(0.3)
    assert not pm.is_alive("auto")


def test_autostart_disabled_by_default(tmp_path, monkeypatch):
    """run_autostart=False (기본값)이면 autostart=true여도 안 뜬다.

    pytest 기반 인증 플로우 테스트가 자식을 띄우지 않도록 보장.
    """
    monkeypatch.setenv(PASSWORD_ENV, "secret")
    script = _make_child_script(tmp_path)
    config = tmp_path / "config.yml"
    _seed_config(config, autostart=True, child_script=script)

    app = backend_app.create_app(
        config_path=config,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "_no_dist",
    )
    pm = app.config["process_manager"]
    time.sleep(0.3)
    assert not pm.is_alive("auto")
