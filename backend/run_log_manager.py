"""ActionRun 단위 로그 파일 관리.

기존 LogManager는 service_id 기준 flat 파일(<id>.log + 회전)을 가정한다.
ActionRun은 ActionItem당 여러 run이 동시에 / 시간차로 일어나며 경로가
`logs/actions/<group_id>/<item_id>/<run_id>.log`처럼 run_id로 구분된다.

이 매니저는 run_id 기준으로 파일을 새로 만들고, 자식 stdout/stderr를 줄 단위로
파이프해서 기록한다. tail / read_since / SSE 스트림 인터페이스는 LogManager와
동일한 형태를 따른다(프론트가 같은 훅을 재활용할 수 있도록).

회전 정책은 다르다. run 끝나면 같은 ActionItem 디렉토리의 .log 파일 개수가
keep_runs를 넘으면 mtime이 오래된 것부터 삭제한다.
"""

from __future__ import annotations

import io
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import IO, Iterator


class RunLogManager:
    def __init__(self, log_root: str | os.PathLike[str]) -> None:
        self._root = Path(log_root)
        self._root.mkdir(parents=True, exist_ok=True)
        # run_id -> 파일 핸들 / 캡처 thread / stop flag
        self._files: dict[str, IO[str]] = {}
        self._readers: dict[str, threading.Thread] = {}
        self._stop_flags: dict[str, threading.Event] = {}
        # run_id -> (group_id, item_id) 캐시 (run 종료 시 keep_runs 정리에 사용)
        self._scopes: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 경로
    # ------------------------------------------------------------------

    def item_dir(self, group_id: str, item_id: str) -> Path:
        return self._root / group_id / item_id

    def log_path(self, group_id: str, item_id: str, run_id: str) -> Path:
        return self.item_dir(group_id, item_id) / f"{run_id}.log"

    # ------------------------------------------------------------------
    # 자식 stdout 캡처
    # ------------------------------------------------------------------

    def start_capture(
        self,
        run_id: str,
        group_id: str,
        item_id: str,
        source: IO[bytes] | IO[str],
    ) -> Path:
        """자식 stdout(PIPE)을 받아 run_id별 .log 파일에 줄 단위로 기록한다.

        반환값은 만들어진 .log 절대 경로.
        """
        path = self.log_path(group_id, item_id, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            self._teardown_locked(run_id)
            fh = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
            self._files[run_id] = fh
            self._scopes[run_id] = (group_id, item_id)
            stop = threading.Event()
            self._stop_flags[run_id] = stop
            t = threading.Thread(
                target=self._reader_loop,
                args=(run_id, source, stop),
                name=f"run-log-reader[{run_id}]",
                daemon=True,
            )
            self._readers[run_id] = t
            t.start()
        return path

    def write_line(self, run_id: str, text: str) -> None:
        """관리 메시지(예: "사용자 취소")를 직접 한 줄 기록한다."""
        if not text.endswith("\n"):
            text = text + "\n"
        with self._lock:
            fh = self._files.get(run_id)
            if fh is None or fh.closed:
                # 파이프가 이미 닫혔다면 직접 파일을 열어서 append
                scope = self._scopes.get(run_id)
                if scope is None:
                    return
                gid, iid = scope
                path = self.log_path(gid, iid, run_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8", errors="replace") as out:
                    out.write(text)
                return
            try:
                fh.write(text)
                fh.flush()
            except Exception:  # noqa: BLE001
                pass

    def stop_capture(self, run_id: str, *, keep_runs: int = 0, join_timeout: float = 5.0) -> None:
        """run 종료 시 호출. 핸들을 닫고, 필요하면 오래된 .log를 정리한다.

        graceful drain:
        - 자식 프로세스가 끝나면 stdout PIPE에 EOF가 도달한다. reader thread는 그 EOF까지
          남은 데이터를 읽고 자연스럽게 종료한다. 여기서 reader thread를 join해 마지막 줄까지
          파일에 기록되도록 보장한 다음 핸들을 닫는다.
        - 만약 join_timeout 안에 끝나지 않으면 (자식이 PIPE를 끊지 않은 채 살아있는 등) stop
          flag를 set하고 강제 정리한다.
        """
        with self._lock:
            scope = self._scopes.get(run_id)
            reader = self._readers.get(run_id)

        if reader is not None and reader.is_alive():
            # lock 밖에서 join. reader가 lock을 잠시 잡아 마지막 줄을 쓰는 시간을 확보.
            reader.join(timeout=join_timeout)

        with self._lock:
            self._teardown_locked(run_id)
        if scope is not None and keep_runs > 0:
            self._enforce_keep_runs(scope[0], scope[1], keep_runs)

    def _teardown_locked(self, run_id: str) -> None:
        # self._lock을 잡은 채로 호출.
        stop = self._stop_flags.pop(run_id, None)
        if stop is not None:
            stop.set()
        self._readers.pop(run_id, None)
        fh = self._files.pop(run_id, None)
        if fh is not None and not fh.closed:
            try:
                fh.flush()
            except Exception:  # noqa: BLE001
                pass
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass

    def _reader_loop(self, run_id: str, source, stop: threading.Event) -> None:
        """자식 stdout PIPE를 EOF까지 읽어 파일에 기록한다.

        설계 메모:
        - `iter(readline, b"")`은 자식이 PIPE를 닫을 때(EOF) 자연 종료한다.
          정상 종료 경로에서는 stop flag를 보지 않고 마지막 줄까지 읽는다.
        - stop flag는 emergency teardown용. 한 줄을 이미 읽었다면 무조건 파일에 쓴다
          (그래야 stop_capture 직전에 도착한 마지막 줄이 유실되지 않는다).
        """
        try:
            for raw in iter(source.readline, b""):
                if not raw:
                    break
                if isinstance(raw, bytes):
                    text = raw.decode("utf-8", errors="replace")
                else:
                    text = raw
                with self._lock:
                    fh = self._files.get(run_id)
                    if fh is None or fh.closed:
                        # 호출자가 강제로 닫았다. 더 쓸 수 없음.
                        break
                    try:
                        fh.write(text)
                    except Exception:  # noqa: BLE001
                        break
                # 한 줄 처리 후에만 stop flag 확인. 마지막 줄을 놓치지 않도록 항상
                # 줄 쓰기 → stop 체크 순서.
                if stop.is_set():
                    break
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                source.close()
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                fh = self._files.get(run_id)
                if fh is not None and not fh.closed:
                    try:
                        fh.flush()
                    except Exception:  # noqa: BLE001
                        pass

    # ------------------------------------------------------------------
    # 보관 정책
    # ------------------------------------------------------------------

    def _enforce_keep_runs(self, group_id: str, item_id: str, keep_runs: int) -> None:
        directory = self.item_dir(group_id, item_id)
        if not directory.is_dir():
            return
        try:
            logs = [p for p in directory.iterdir() if p.is_file() and p.suffix == ".log"]
        except OSError:
            return
        if len(logs) <= keep_runs:
            return
        logs.sort(key=lambda p: p.stat().st_mtime)
        excess = len(logs) - keep_runs
        for old in logs[:excess]:
            try:
                old.unlink()
            except OSError:
                continue

    # ------------------------------------------------------------------
    # 읽기
    # ------------------------------------------------------------------

    def tail(self, group_id: str, item_id: str, run_id: str, lines: int) -> tuple[list[str], int]:
        path = self.log_path(group_id, item_id, run_id)
        if lines <= 0 or not path.is_file():
            return [], 0

        tail_lines: deque[str] = deque(maxlen=lines)
        with path.open("rb") as fh:
            fh.seek(0, io.SEEK_END)
            end = fh.tell()
            block = 4096
            data = b""
            pos = end
            while pos > 0 and len(tail_lines) <= lines:
                read_size = min(block, pos)
                pos -= read_size
                fh.seek(pos)
                data = fh.read(read_size) + data
                if data.count(b"\n") > lines:
                    break
            text = data.decode("utf-8", errors="replace")
            for line in text.splitlines():
                tail_lines.append(line)

        return list(tail_lines)[-lines:], end

    def read_since(
        self, group_id: str, item_id: str, run_id: str, offset: int
    ) -> tuple[list[str], int]:
        path = self.log_path(group_id, item_id, run_id)
        if not path.is_file():
            return [], 0
        size = path.stat().st_size
        start = offset if 0 <= offset <= size else 0
        if start == size:
            return [], size
        with path.open("rb") as fh:
            fh.seek(start)
            data = fh.read()
        text = data.decode("utf-8", errors="replace")
        return text.splitlines(), size

    def stream(
        self,
        group_id: str,
        item_id: str,
        run_id: str,
        *,
        from_start: bool = True,
        idle_sleep: float = 0.5,
        keepalive_after: float = 15.0,
        stop_event=None,
        is_running: callable | None = None,
    ) -> Iterator[str | None]:
        """run의 stdout을 SSE로 흘려보낸다.

        run이 끝나고(`is_running`이 False) 더 보낼 줄이 없으면 generator를 종료한다.
        그래야 클라이언트의 EventSource가 자연스럽게 닫힌다.
        """
        path = self.log_path(group_id, item_id, run_id)
        # 파일이 아직 안 만들어졌으면 잠깐 기다린다 (run 시작 직후 race).
        deadline = time.time() + 5.0
        while not path.is_file():
            if stop_event is not None and stop_event.is_set():
                return
            if time.time() > deadline:
                return
            time.sleep(0.1)

        offset = 0 if from_start else path.stat().st_size
        last_data_at = time.time()

        while True:
            if stop_event is not None and stop_event.is_set():
                return
            lines, offset = self.read_since(group_id, item_id, run_id, offset)
            if lines:
                last_data_at = time.time()
                for line in lines:
                    yield line
            else:
                if is_running is not None and not is_running():
                    # run이 끝났고 추가 줄이 없으면 종료.
                    return
                if time.time() - last_data_at > keepalive_after:
                    last_data_at = time.time()
                    yield None
                time.sleep(idle_sleep)

    # ------------------------------------------------------------------
    # 정리
    # ------------------------------------------------------------------

    def close_all(self) -> None:
        with self._lock:
            for run_id in list(self._stop_flags.keys()):
                self._teardown_locked(run_id)


__all__ = ["RunLogManager"]
