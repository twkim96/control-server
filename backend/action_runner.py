"""ActionItem(일회성 명령) 실행과 라이프사이클 관리.

설계 메모:
* 같은 ActionItem을 두 번 클릭하면 별도 run_id로 동시에 실행 가능.
* `Popen(shell=False, start_new_session=True)`. 자식은 새 process group에 속한다.
* 중지: process group에 SIGINT → 5초 대기 → SIGTERM → 2초 대기 → SIGKILL.
* 메타데이터(run_id, started_at, ended_at, exit_code, status)는 메모리에 보관.
  컨트롤 서버 재시작 시 사라진다 (디스크 .log 파일은 남는다).
* keep_runs를 넘으면 같은 ActionItem 디렉토리의 오래된 .log 자동 삭제.
* 종료 감시는 run당 백그라운드 thread 하나가 popen.wait()를 돌고, 끝나면
  status를 succeeded/failed로 갱신하고 keep_runs 정리를 트리거한다.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from config_loader import (
    ActionGroupConfig,
    ActionItemConfig,
    validate_command_safety,
)
from run_log_manager import RunLogManager


_log = logging.getLogger("server_control.actions")


# 가능한 상태:
# - running   : 자식이 살아있음
# - succeeded : exit_code == 0
# - failed    : exit_code != 0
# - cancelled : 사용자가 cancel API로 중지
RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCEEDED = "succeeded"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"


class ActionRunError(RuntimeError):
    """ActionRun 처리 중 기대 가능한 오류."""


@dataclass
class ActionRun:
    run_id: str
    group_id: str
    item_id: str
    started_at: float
    ended_at: float | None = None
    exit_code: int | None = None
    status: str = RUN_STATUS_RUNNING
    log_path: str | None = None
    cancel_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return round(self.ended_at - self.started_at, 3)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration_seconds"] = self.duration_seconds
        return d


class ActionRunner:
    def __init__(
        self,
        run_log_manager: RunLogManager,
        *,
        clock: callable = time.time,
        max_runs_per_item: int = 50,
    ) -> None:
        self._run_log = run_log_manager
        self._clock = clock
        self._max_runs_per_item = max_runs_per_item
        self._lock = threading.RLock()
        # run_id -> ActionRun
        self._runs: dict[str, ActionRun] = {}
        # run_id -> Popen
        self._popens: dict[str, subprocess.Popen] = {}
        # (group_id, item_id) -> 최근 run_id 큐 (최신이 끝)
        self._by_item: dict[tuple[str, str], list[str]] = {}
        # run_id -> ActionItemConfig 사본 (keep_runs 등 종료 시 필요)
        self._item_cache: dict[str, ActionItemConfig] = {}
        # run_id -> waiter thread (참조 유지용)
        self._waiters: dict[str, threading.Thread] = {}

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> ActionRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self, group_id: str, item_id: str) -> list[ActionRun]:
        with self._lock:
            ids = list(self._by_item.get((group_id, item_id), ()))
            return [self._runs[r] for r in ids if r in self._runs]

    def is_running(self, run_id: str) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            return run is not None and run.status == RUN_STATUS_RUNNING

    # ------------------------------------------------------------------
    # 실행
    # ------------------------------------------------------------------

    def start(
        self,
        group: ActionGroupConfig,
        item: ActionItemConfig,
    ) -> ActionRun:
        # 등록 시 검증을 통과한 명령이지만, 직접 편집된 config로 들어온 경우를 대비해
        # 실행 시점에 한 번 더 검증.
        try:
            validate_command_safety(
                item.command,
                kind=item.kind,
                where=f"actions[{group.id}].items[{item.id}]",
            )
        except Exception as exc:  # noqa: BLE001
            raise ActionRunError(f"command 검증 실패: {exc}") from exc

        cwd = self._resolve_cwd(item)
        if cwd is not None and not Path(cwd).is_dir():
            raise ActionRunError(f"working directory가 존재하지 않습니다: {cwd}")

        env = os.environ.copy()
        env.update(item.env)
        # 파이썬 자식이라도 실시간 stdout 보장
        env.setdefault("PYTHONUNBUFFERED", "1")

        run_id = uuid.uuid4().hex[:16]
        try:
            popen = subprocess.Popen(
                list(item.command),
                cwd=str(cwd) if cwd else None,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                start_new_session=True,
                close_fds=True,
                shell=False,
            )
        except (OSError, FileNotFoundError) as exc:
            raise ActionRunError(f"실행 실패: {exc}") from exc

        log_path: Path | None = None
        if item.log.enabled:
            assert popen.stdout is not None
            log_path = self._run_log.start_capture(
                run_id, group.id, item.id, popen.stdout
            )

        run = ActionRun(
            run_id=run_id,
            group_id=group.id,
            item_id=item.id,
            started_at=self._clock(),
            log_path=str(log_path) if log_path else None,
        )

        with self._lock:
            self._runs[run_id] = run
            self._popens[run_id] = popen
            self._item_cache[run_id] = item
            queue = self._by_item.setdefault((group.id, item.id), [])
            queue.append(run_id)
            # 메모리 상의 메타데이터 N개 제한 (오래된 run 제거; 디스크 .log는 별도 정책)
            while len(queue) > self._max_runs_per_item:
                old = queue.pop(0)
                self._runs.pop(old, None)
                self._popens.pop(old, None)
                self._item_cache.pop(old, None)
                self._waiters.pop(old, None)

        waiter = threading.Thread(
            target=self._wait_loop,
            args=(run_id, popen),
            name=f"action-waiter[{run_id}]",
            daemon=True,
        )
        with self._lock:
            self._waiters[run_id] = waiter
        waiter.start()

        _log.info(
            "action started: %s/%s run_id=%s pid=%s",
            group.id,
            item.id,
            run_id,
            popen.pid,
        )
        return run

    def _resolve_cwd(self, item: ActionItemConfig) -> str | None:
        if item.cwd:
            return item.cwd
        return None

    def _wait_loop(self, run_id: str, popen: subprocess.Popen) -> None:
        try:
            exit_code = popen.wait()
        except Exception as exc:  # noqa: BLE001
            _log.warning("action waiter exception: run_id=%s err=%s", run_id, exc)
            exit_code = popen.poll()

        # 핵심 순서:
        # 1) 자식 종료 → 2) stop_capture가 reader thread를 join하여 PIPE EOF까지 drain
        # 3) 그 후에 status를 갱신 + exit code 한 줄 기록.
        # 이 순서를 지켜야 외부에서 status=succeeded/failed를 본 시점에 .log 파일이
        # 자식 stdout 마지막 줄까지 모두 포함한다.
        with self._lock:
            item = self._item_cache.get(run_id)
        keep_runs = item.log.keep_runs if item is not None else 0
        self._run_log.stop_capture(run_id, keep_runs=keep_runs)

        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run.ended_at = self._clock()
            run.exit_code = exit_code
            if run.status == RUN_STATUS_RUNNING:
                if exit_code == 0:
                    run.status = RUN_STATUS_SUCCEEDED
                else:
                    run.status = RUN_STATUS_FAILED

        # exit code 한 줄을 .log 끝에 남긴다. reader는 이미 join됐으므로
        # 이 write가 마지막 데이터.
        if item is not None and item.log.enabled and run is not None:
            tail_msg = f"\n[exit_code={exit_code} status={run.status}]\n"
            self._run_log.write_line(run_id, tail_msg)

        _log.info(
            "action ended: run_id=%s exit_code=%s status=%s",
            run_id,
            exit_code,
            run.status if run else "?",
        )

    # ------------------------------------------------------------------
    # 중지
    # ------------------------------------------------------------------

    def cancel(self, run_id: str, *, poll_interval: float = 0.2) -> ActionRun:
        with self._lock:
            run = self._runs.get(run_id)
            popen = self._popens.get(run_id)
            if run is None:
                raise ActionRunError(f"run을 찾을 수 없습니다: {run_id}")
            if run.status != RUN_STATUS_RUNNING or popen is None:
                # 이미 끝남
                return run
            # 시그널 직전에 미리 cancelled로 표시.
            # 자식이 SIGINT로 비정상 종료하면 _wait_loop이 status를 FAILED로 덮어쓸 수 있어
            # 사용자 의도가 사라진다. 미리 표시해두면 _wait_loop은 RUNNING이 아니므로 통과.
            run.status = RUN_STATUS_CANCELLED
            run.cancel_reason = "user_cancelled"

        sequence = [
            (signal.SIGINT, 5.0),
            (signal.SIGTERM, 2.0),
            (signal.SIGKILL, 2.0),
        ]
        try:
            pgid = os.getpgid(popen.pid)
        except ProcessLookupError:
            with self._lock:
                run = self._runs.get(run_id)
                if run is not None and run.status == RUN_STATUS_RUNNING:
                    run.status = RUN_STATUS_CANCELLED
                    run.cancel_reason = "user_cancelled"
                    run.ended_at = self._clock()
            return run  # type: ignore[return-value]

        # 중지 메시지 한 줄 기록
        self._run_log.write_line(run_id, "[cancel] 사용자 취소 요청 (SIGINT)\n")

        for sig, wait in sequence:
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                break
            except PermissionError as exc:
                raise ActionRunError(
                    f"PID {popen.pid}에 시그널을 보낼 권한이 없습니다: {exc}"
                ) from exc

            deadline = self._clock() + wait
            while self._clock() < deadline:
                if popen.poll() is not None:
                    break
                time.sleep(poll_interval)
            if popen.poll() is not None:
                break

        if popen.poll() is None:
            raise ActionRunError(
                f"run {run_id} 중지 실패: SIGKILL 후에도 살아있습니다."
            )

        with self._lock:
            run = self._runs.get(run_id)
        return run  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # 종료 시 정리
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        with self._lock:
            run_ids = list(self._popens.keys())
        for run_id in run_ids:
            try:
                self.cancel(run_id)
            except Exception:  # noqa: BLE001
                pass
        self._run_log.close_all()


__all__ = [
    "ActionRun",
    "ActionRunError",
    "ActionRunner",
    "RUN_STATUS_RUNNING",
    "RUN_STATUS_SUCCEEDED",
    "RUN_STATUS_FAILED",
    "RUN_STATUS_CANCELLED",
]
