from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_cloudflare_launcher_passes_local_url_host_and_port(tmp_path: Path) -> None:
    capture = tmp_path / "bind.txt"
    cloudflared = _executable(
        tmp_path / "cloudflared",
        "#!/usr/bin/env bash\n"
        "echo 'https://fixture.trycloudflare.com'\n"
        "while true; do sleep 1; done\n",
    )
    devspace = _executable(
        tmp_path / "devspace",
        "#!/usr/bin/env bash\n"
        "printf '%s:%s' \"$HOST\" \"$PORT\" > \"$CAPTURE\"\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "CLOUDFLARED_BIN": str(cloudflared),
            "DEVSPACE_BIN": str(devspace),
            "DEVSPACE_LOCAL_URL": "http://127.0.0.1:8765",
            "DEVSPACE_TUNNEL_RUNTIME_DIR": str(tmp_path / "runtime"),
            "CAPTURE": str(capture),
        }
    )

    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "run_devspace_cloudflare.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert capture.read_text(encoding="utf-8") == "127.0.0.1:8765"


def test_ngrok_launcher_requires_explicit_public_base_url(tmp_path: Path) -> None:
    ngrok = _executable(tmp_path / "ngrok", "#!/usr/bin/env bash\nexit 0\n")
    devspace = _executable(tmp_path / "devspace", "#!/usr/bin/env bash\nexit 0\n")
    env = os.environ.copy()
    env.update(
        {
            "NGROK_BIN": str(ngrok),
            "DEVSPACE_BIN": str(devspace),
            "DEVSPACE_LOCAL_URL": "http://127.0.0.1:8765",
            "DEVSPACE_PUBLIC_BASE_URL": "",
        }
    )

    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "run_devspace_ngrok.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 2
    assert "DEVSPACE_PUBLIC_BASE_URL is required" in result.stderr
