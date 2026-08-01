from pathlib import Path

from app import (
    APP_VERSION,
    DEFAULT_CONFIG_PATH,
    DEFAULT_FRONTEND_DIST,
    DEFAULT_LOG_DIR,
    DEFAULT_RUNTIME_DIR,
    resolve_app_paths,
)


def test_app_version_matches_release_metadata() -> None:
    assert APP_VERSION == "1.5.1"


def test_app_paths_keep_checkout_defaults() -> None:
    paths = resolve_app_paths(environ={})

    assert paths.config_path == DEFAULT_CONFIG_PATH
    assert paths.log_dir == DEFAULT_LOG_DIR
    assert paths.runtime_dir == DEFAULT_RUNTIME_DIR
    assert paths.frontend_dist == DEFAULT_FRONTEND_DIST


def test_app_paths_accept_managed_environment(tmp_path: Path) -> None:
    paths = resolve_app_paths(
        environ={
            "CONTROL_CONFIG_PATH": str(tmp_path / "config" / "config.yml"),
            "CONTROL_LOG_DIR": str(tmp_path / "logs"),
            "CONTROL_RUNTIME_DIR": str(tmp_path / "runtime"),
            "CONTROL_FRONTEND_DIST": str(tmp_path / "current" / "frontend" / "dist"),
        }
    )

    assert paths.config_path == tmp_path / "config" / "config.yml"
    assert paths.log_dir == tmp_path / "logs"
    assert paths.runtime_dir == tmp_path / "runtime"
    assert paths.frontend_dist == tmp_path / "current" / "frontend" / "dist"


def test_explicit_app_paths_override_environment(tmp_path: Path) -> None:
    paths = resolve_app_paths(
        config_path=tmp_path / "explicit.yml",
        log_dir=tmp_path / "explicit-logs",
        runtime_dir=tmp_path / "explicit-runtime",
        frontend_dist=tmp_path / "explicit-dist",
        environ={
            "CONTROL_CONFIG_PATH": "/ignored/config.yml",
            "CONTROL_LOG_DIR": "/ignored/logs",
            "CONTROL_RUNTIME_DIR": "/ignored/runtime",
            "CONTROL_FRONTEND_DIST": "/ignored/dist",
        },
    )

    assert paths.config_path == tmp_path / "explicit.yml"
    assert paths.log_dir == tmp_path / "explicit-logs"
    assert paths.runtime_dir == tmp_path / "explicit-runtime"
    assert paths.frontend_dist == tmp_path / "explicit-dist"
