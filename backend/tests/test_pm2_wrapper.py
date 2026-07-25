from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_wrapper_applies_taskpolicy_only_to_pinned_pm2_cli(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    wrapper = repo / "scripts" / "pm2ctl.sh"
    node = _executable(tmp_path / "node", "#!/bin/sh\nexit 0\n")
    cli = tmp_path / "pm2"
    cli.write_text("// fixture\n", encoding="utf-8")
    capture = tmp_path / "argv.txt"
    taskpolicy = _executable(
        tmp_path / "taskpolicy",
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE_PATH"\n',
    )
    short_home = Path("/tmp") / f"sc-pm2-wrapper-{os.getpid()}-{tmp_path.name[-6:]}"
    env = {
        **os.environ,
        "CONTROL_PM2_NODE": str(node),
        "CONTROL_PM2_CLI": str(cli),
        "CONTROL_PM2_HOME": str(short_home),
        "CONTROL_PM2_TASKPOLICY": str(taskpolicy),
        "CAPTURE_PATH": str(capture),
    }

    try:
        result = subprocess.run(
            [str(wrapper), "jlist"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    finally:
        shutil.rmtree(short_home, ignore_errors=True)

    assert result.returncode == 0
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "-a",
        str(node),
        str(cli),
        "jlist",
    ]
