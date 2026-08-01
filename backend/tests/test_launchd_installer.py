from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_launchd_renderer_supports_managed_paths(tmp_path: Path) -> None:
    home = tmp_path / "home"
    data = tmp_path / "managed & data"
    plist_path = tmp_path / "rendered.plist"
    env_path = tmp_path / "run.env"
    env_path.write_text(
        "CONTROL_PASSWORD=test-password\nCONTROL_PROCESS_BACKEND=pm2\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CONTROL_APP_ROOT": str(REPO_ROOT),
            "CONTROL_ENV_FILE": str(env_path),
            "CONTROL_CONFIG_PATH": str(data / "config.yml"),
            "CONTROL_LOG_DIR": str(data / "logs"),
            "CONTROL_RUNTIME_DIR": str(data / "runtime"),
            "CONTROL_FRONTEND_DIST": str(data / "frontend" / "dist"),
            "CONTROL_PYTHON": sys.executable,
            "CONTROL_PM2_NODE": subprocess.check_output(
                ["/usr/bin/which", "node"], text=True
            ).strip(),
            "CONTROL_LAUNCHD_LABEL": "io.github.twkim96.control-server.test",
            "CONTROL_PLIST_DST": str(plist_path),
        }
    )

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "launchd" / "install.sh"), "render"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["Label"] == "io.github.twkim96.control-server.test"
    args = payload["ProgramArguments"]
    assert args[args.index("--config") + 1] == str(data / "config.yml")
    assert args[args.index("--log-dir") + 1] == str(data / "logs")
    assert args[args.index("--runtime-dir") + 1] == str(data / "runtime")
    assert args[args.index("--frontend-dist") + 1] == str(
        data / "frontend" / "dist"
    )
    assert payload["EnvironmentVariables"]["CONTROL_PROCESS_BACKEND"] == "pm2"
    assert "FILE_CHECK_NOVELPIA_PASSWORD" not in payload["EnvironmentVariables"]
    assert plist_path.stat().st_mode & 0o777 == 0o600


def test_launchd_renderer_preserves_existing_plist_on_render_failure(
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "existing.plist"
    plist_path.write_text("previous plist", encoding="utf-8")
    env_path = tmp_path / "run.env"
    env_path.write_text("CONTROL_PASSWORD=test-password\n", encoding="utf-8")
    broken_python = tmp_path / "broken-python"
    broken_python.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    broken_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "CONTROL_APP_ROOT": str(REPO_ROOT),
            "CONTROL_ENV_FILE": str(env_path),
            "CONTROL_PYTHON": str(broken_python),
            "CONTROL_PLIST_DST": str(plist_path),
        }
    )

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "launchd" / "install.sh"), "render"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert plist_path.read_text(encoding="utf-8") == "previous plist"
    assert not list(tmp_path.glob("existing.plist.tmp.*"))
