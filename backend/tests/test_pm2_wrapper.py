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


def test_wrapper_python_lock_fallback_preserves_exclusion(tmp_path: Path) -> None:
    import fcntl
    import sys
    import tempfile
    repo = Path(__file__).resolve().parents[2]
    fake_repo = tmp_path / "app"
    (fake_repo / "scripts").mkdir(parents=True)
    (fake_repo / "backend").mkdir()
    wrapper = fake_repo / "scripts/pm2ctl.sh"
    wrapper.write_text((repo / "scripts/pm2ctl.sh").read_text().replace('/usr/bin/lockf', '/no-such-control-server-lockf'))
    wrapper.chmod(0o755)
    shutil.copy2(repo / "backend/file_lock.py", fake_repo / "backend/file_lock.py")
    node = _executable(tmp_path / "node", "#!/bin/sh\nexit 0\n")
    cli = tmp_path / "pm2"
    cli.write_text("// fixture\n")
    capture = tmp_path / "argv.txt"
    taskpolicy = _executable(tmp_path / "taskpolicy", '#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE_PATH"\n')
    with tempfile.TemporaryDirectory(prefix="sc-flock-", dir="/private/tmp") as runtime:
        home = Path(runtime) / "pm2"
        engine = Path(runtime) / "pm2-engine"
        engine.mkdir()
        (engine / "maintenance-owner").write_text("fixture-owner")
        env = {**os.environ, "CONTROL_PYTHON": sys.executable, "CONTROL_PM2_NODE": str(node),
               "CONTROL_PM2_CLI": str(cli), "CONTROL_PM2_HOME": str(home),
               "CONTROL_PM2_TASKPOLICY": str(taskpolicy), "CAPTURE_PATH": str(capture)}
        with (engine / "maintenance.lock").open("a") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            blocked = subprocess.run([str(wrapper), 'jlist'], env=env, capture_output=True, timeout=5)
            assert blocked.returncode == 75
            assert not capture.exists()
        allowed = subprocess.run([str(wrapper), 'jlist'], env=env, capture_output=True, timeout=5)
        assert allowed.returncode == 0, allowed.stderr.decode()
        assert capture.read_text().splitlines() == ['-a', str(node), str(cli), 'jlist']
