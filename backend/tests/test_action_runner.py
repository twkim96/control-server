"""ActionRunner 통합 테스트.

자식 프로세스를 실제로 띄워 stdout 캡처, exit code, cancel을 검증한다.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from action_runner import (
    RUN_STATUS_CANCELLED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCEEDED,
    ActionRunError,
    ActionRunner,
)
from config_loader import (
    ActionGroupConfig,
    ActionItemConfig,
    ActionRunLogConfig,
)
from run_log_manager import RunLogManager


def _build_item(
    *,
    item_id: str = "echo",
    kind: str = "argv",
    command: tuple[str, ...] = ("echo", "hello"),
    cwd: str | None = None,
    keep_runs: int = 5,
) -> ActionItemConfig:
    return ActionItemConfig(
        id=item_id,
        name=item_id,
        description="",
        kind=kind,
        cwd=cwd,
        command=command,
        env={},
        log=ActionRunLogConfig(enabled=True, keep_runs=keep_runs),
        external_logs=(),
    )


def _build_group(item: ActionItemConfig, group_id: str = "g") -> ActionGroupConfig:
    return ActionGroupConfig(id=group_id, name=group_id, description="", items=(item,))


def _wait_until_done(runner: ActionRunner, run_id: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = runner.get_run(run_id)
        if run is not None and run.status != RUN_STATUS_RUNNING:
            return
        time.sleep(0.05)
    raise AssertionError(f"run {run_id}이 {timeout}s 안에 끝나지 않았습니다.")


def _write_script(tmp_path: Path, body: str, name: str = "script.py") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_run_succeeds_and_captures_stdout(tmp_path: Path) -> None:
    rl = RunLogManager(tmp_path / "logs")
    runner = ActionRunner(rl)

    finish = tmp_path / "finish"
    script = _write_script(tmp_path,
        "import pathlib, time\nprint('hello world', flush=True)\n"
        f"while not pathlib.Path({str(finish)!r}).exists(): time.sleep(0.01)\n")
    item = _build_item(command=(sys.executable, str(script)))
    group = _build_group(item)

    run = runner.start(group, item)
    assert run.status == RUN_STATUS_RUNNING
    finish.touch()
    _wait_until_done(runner, run.run_id)

    final = runner.get_run(run.run_id)
    assert final is not None
    assert final.status == RUN_STATUS_SUCCEEDED
    assert final.exit_code == 0

    log_path = Path(final.log_path or "")
    assert log_path.is_file()
    content = log_path.read_text(encoding="utf-8")
    assert "hello world" in content


def test_large_stdout_is_fully_captured(tmp_path: Path) -> None:
    """GPT 크로스체크 반영: 자식이 종료 직전에 쏟아낸 stdout이 유실되지 않는지 검증.

    `_wait_loop`은 `popen.wait()` 이후 `stop_capture`를 호출하고, 이때 reader thread를
    join해서 EOF까지 drain되도록 보장한다. 이 테스트는 그 contract를 잠근다.
    """
    rl = RunLogManager(tmp_path / "logs")
    runner = ActionRunner(rl)

    line_count = 5000
    script = _write_script(
        tmp_path,
        "import sys\n"
        f"for i in range({line_count}):\n"
        "    print(f'line-{i:05d}')\n",
        name="dump.py",
    )
    item = _build_item(
        command=(sys.executable, "-u", str(script)),
        kind="python",
        cwd=str(tmp_path),
    )
    group = _build_group(item)
    run = runner.start(group, item)
    _wait_until_done(runner, run.run_id, timeout=15.0)

    final = runner.get_run(run.run_id)
    assert final is not None
    assert final.status == RUN_STATUS_SUCCEEDED
    log_path = Path(final.log_path or "")
    content = log_path.read_text(encoding="utf-8")
    written = [line for line in content.splitlines() if line.startswith("line-")]
    assert len(written) == line_count, f"expected {line_count} lines, got {len(written)}"
    # 마지막 줄도 살아있는지 확인 (drain 종료 시점 race가 핵심).
    assert f"line-{line_count - 1:05d}" in content


def test_failed_run_marks_failed(tmp_path: Path) -> None:
    rl = RunLogManager(tmp_path / "logs")
    runner = ActionRunner(rl)

    script = _write_script(tmp_path, "import sys\nsys.exit(3)\n", name="fail.py")
    item = _build_item(
        command=(sys.executable, str(script)),
        kind="python",
        cwd=str(tmp_path),
    )
    group = _build_group(item)

    run = runner.start(group, item)
    _wait_until_done(runner, run.run_id)
    final = runner.get_run(run.run_id)
    assert final is not None
    assert final.status == RUN_STATUS_FAILED
    assert final.exit_code == 3


def test_cancel_marks_cancelled(tmp_path: Path) -> None:
    rl = RunLogManager(tmp_path / "logs")
    runner = ActionRunner(rl)

    script = _write_script(tmp_path, "import time\ntime.sleep(5)\n", name="sleep.py")
    item = _build_item(
        command=(sys.executable, str(script)),
        kind="python",
        cwd=str(tmp_path),
    )
    group = _build_group(item)

    run = runner.start(group, item)
    time.sleep(0.3)
    runner.cancel(run.run_id)
    _wait_until_done(runner, run.run_id)
    final = runner.get_run(run.run_id)
    assert final is not None
    assert final.status == RUN_STATUS_CANCELLED


def test_concurrent_runs_independent(tmp_path: Path) -> None:
    rl = RunLogManager(tmp_path / "logs")
    runner = ActionRunner(rl)

    script = _write_script(
        tmp_path,
        "import time, os\nprint(os.getpid())\ntime.sleep(0.3)\n",
        name="conc.py",
    )
    item = _build_item(
        command=(sys.executable, "-u", str(script)),
        kind="python",
        cwd=str(tmp_path),
    )
    group = _build_group(item)

    run_a = runner.start(group, item)
    run_b = runner.start(group, item)
    assert run_a.run_id != run_b.run_id

    _wait_until_done(runner, run_a.run_id)
    _wait_until_done(runner, run_b.run_id)
    a = runner.get_run(run_a.run_id)
    b = runner.get_run(run_b.run_id)
    assert a is not None and b is not None
    assert a.status == RUN_STATUS_SUCCEEDED
    assert b.status == RUN_STATUS_SUCCEEDED


def test_keep_runs_evicts_oldest(tmp_path: Path) -> None:
    rl = RunLogManager(tmp_path / "logs")
    runner = ActionRunner(rl)

    item = _build_item(command=("echo", "hi"), keep_runs=2)
    group = _build_group(item)

    run_ids = []
    for _ in range(4):
        run = runner.start(group, item)
        _wait_until_done(runner, run.run_id)
        run_ids.append(run.run_id)
        # 각 run의 mtime이 명확히 구분되도록 약간의 sleep
        time.sleep(0.05)

    log_dir = rl.item_dir(group.id, item.id)
    logs = sorted(log_dir.glob("*.log"))
    assert len(logs) == 2


def test_disabled_log_does_not_block_large_output(tmp_path: Path) -> None:
    runner = ActionRunner(RunLogManager(tmp_path / "logs"))
    script = _write_script(tmp_path, "import os\nos.write(1, b'x' * 1048576)\n")
    item = replace(
        _build_item(command=(sys.executable, str(script))),
        log=ActionRunLogConfig(enabled=False, keep_runs=2),
    )
    try:
        run = runner.start(_build_group(item), item)
        _wait_until_done(runner, run.run_id)
        assert run.status == RUN_STATUS_SUCCEEDED
        assert run.exit_code == 0
        assert run.log_path is None
    finally:
        runner.shutdown()


@pytest.mark.parametrize("cancelling", [False, True])
def test_metadata_retention_preserves_unfinished_runs(tmp_path: Path, monkeypatch, cancelling) -> None:
    runner = ActionRunner(RunLogManager(tmp_path / "logs"), max_runs_per_item=1)
    script = _write_script(tmp_path, "import time\ntime.sleep(30)\n")
    item = _build_item(command=(sys.executable, str(script)))
    group = _build_group(item)
    first = runner.start(group, item)
    first_waiter = runner._waiters[first.run_id]
    signal_entered = threading.Event()
    release_signal = threading.Event()
    cancel_thread = None
    errors = []
    original_killpg = os.killpg

    def blocked_signal(pgid, sig):
        signal_entered.set()
        assert release_signal.wait(5)
        original_killpg(pgid, sig)

    def cancel_first():
        try:
            runner.cancel(first.run_id)
        except Exception as exc:
            errors.append(exc)

    try:
        if cancelling:
            monkeypatch.setattr(os, "killpg", blocked_signal)
            cancel_thread = threading.Thread(target=cancel_first)
            cancel_thread.start()
            assert signal_entered.wait(2)
            assert first.status == RUN_STATUS_CANCELLED
        quick = replace(item, command=("echo", "done"))
        second = runner.start(group, quick)
        deadline = time.monotonic() + 3
        while second.ended_at is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert second.ended_at is not None
        assert runner.get_run(first.run_id) is first
        assert first.ended_at is None
        assert first.run_id in runner._popens
        # Finished history is bounded even while the older run remains active.
        assert len(runner.list_runs(group.id, item.id)) == 1
        release_signal.set()
        if cancel_thread is not None:
            cancel_thread.join(3)
            assert not cancel_thread.is_alive()
            assert not errors
        else:
            runner.cancel(first.run_id)
        first_waiter.join(3)
        assert not first_waiter.is_alive()
        assert first.ended_at is not None
        assert first.exit_code is not None
    finally:
        release_signal.set()
        if cancel_thread is not None:
            cancel_thread.join(3)
        runner.shutdown()


def test_log_retention_preserves_active_capture_and_bounds_completed(tmp_path: Path) -> None:
    runner = ActionRunner(RunLogManager(tmp_path / "logs"))
    release = tmp_path / "release"
    script = _write_script(
        tmp_path,
        "import pathlib, sys, time\n"
        "print('original output', flush=True)\n"
        "while not pathlib.Path(sys.argv[1]).exists(): time.sleep(0.01)\n"
        "print('final output', flush=True)\n",
    )
    item = _build_item(command=(sys.executable, str(script), str(release)), keep_runs=1)
    group = _build_group(item)
    try:
        first = runner.start(group, item)
        first_waiter = runner._waiters[first.run_id]
        path = Path(first.log_path)
        deadline = time.monotonic() + 3
        while 'original output' not in path.read_text() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert 'original output' in path.read_text()
        quick = replace(item, command=("echo", "quick"))
        for _ in range(2):
            other = runner.start(group, quick)
            waiter = runner._waiters[other.run_id]
            waiter.join(3)
            assert not waiter.is_alive()
            assert path.is_file()
            assert 'original output' in path.read_text()
            assert len(list(path.parent.glob('*.log'))) == 2
        release.touch()
        first_waiter.join(3)
        assert not first_waiter.is_alive()
        assert first.status == RUN_STATUS_SUCCEEDED
        assert 'original output' in path.read_text()
        assert 'final output' in path.read_text()
        assert '[exit_code=0 status=succeeded]' in path.read_text()
        assert len(list(path.parent.glob('*.log'))) == 1
    finally:
        release.touch()
        runner.shutdown()


def test_unsafe_command_rejected_at_runtime(tmp_path: Path) -> None:
    rl = RunLogManager(tmp_path / "logs")
    runner = ActionRunner(rl)
    # config_loader를 우회해서 sudo가 박힌 ActionItem이 들어왔다고 가정.
    item = _build_item(command=("sudo", "ls"))
    group = _build_group(item)
    with pytest.raises(ActionRunError):
        runner.start(group, item)


def test_missing_cwd_rejected(tmp_path: Path) -> None:
    rl = RunLogManager(tmp_path / "logs")
    runner = ActionRunner(rl)
    script = _write_script(tmp_path, "print(1)\n", name="hello.py")
    item = _build_item(
        command=(sys.executable, str(script)),
        kind="python",
        cwd=str(tmp_path / "nonexistent"),
    )
    group = _build_group(item)
    with pytest.raises(ActionRunError):
        runner.start(group, item)


# 환경 격리: 컨트롤 서버 PID 보호 같은 가드는 process_manager에 있고 여기서는 자식뿐이라 스킵.


@pytest.fixture(autouse=True)
def _restore_cwd():
    cwd = os.getcwd()
    yield
    os.chdir(cwd)
