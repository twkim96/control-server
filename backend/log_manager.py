"""서비스별 로그 파일 관리.

현재 캡처 모델:

* 자식 시작 직전 LogManager가 `<id>.log`를 append 모드로 직접 open한다.
* 그 OS fd를 ProcessManager에 넘기고, ProcessManager는 `Popen(stdout=<fd>, stderr=STDOUT)`로
  자식 stdout/stderr를 파일에 *직접* redirect한다.
* 자식은 컨트롤 서버 프로세스를 거치지 않고 OS가 fd를 잡고 있으므로, 컨트롤 서버가
  죽어도 자식이 SIGPIPE를 맞고 같이 죽는 일이 없다.
* LogManager는 자식이 살아있는 동안 reader thread를 돌리지 않는다. SSE는 디스크에서
  read_since로 끌어다 쓰는 기존 방식 그대로 동작.

회전 정책:

* 자식이 살아있는 동안엔 회전하지 않는다. 자식이 fd를 잡고 있어서 .log → .log.1 rename은
  자식이 보는 inode를 바꾸지 못한다 (자식은 이전 inode에 계속 append).
* 자식이 종료된 시점에 LogManager가 .log 크기를 보고 max_bytes를 넘었으면 회전한다.
* 그래서 always_on 서비스의 .log는 stop/restart 전까지 누적될 수 있다. 로그 폭주가
  우려되면 외부 logrotate 사용을 권장한다.

브라우저에서 임의 파일을 읽지 못하도록, 외부에서 받은 service_id는 사용 전에
ServiceRegistry로 검증해야 한다. 이 모듈은 service_id가 이미 검증되었다고 가정한다.
"""

from __future__ import annotations

import io
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Iterator


class LogManager:
    def __init__(self, log_root: str | os.PathLike[str]) -> None:
        self._root = Path(log_root)
        self._root.mkdir(parents=True, exist_ok=True)
        # service_id -> 회전 정책 (자식 종료 시 사용)
        self._policies: dict[str, _RotatePolicy] = {}
        self._lock = threading.Lock()

    def log_path(self, service_id: str) -> Path:
        return self._root / f"{service_id}.log"

    def rotated_path(self, service_id: str, n: int) -> Path:
        return self._root / f"{service_id}.log.{n}"

    # ------------------------------------------------------------------
    # 자식 stdout fd 준비
    # ------------------------------------------------------------------

    def open_for_child(
        self,
        service_id: str,
        *,
        max_bytes: int,
        keep: int,
    ) -> int:
        """자식이 stdout으로 쓸 append 모드 OS fd를 만들어 반환한다.

        호출자(process_manager)는 이 fd를 `Popen(stdout=fd, stderr=subprocess.STDOUT,
        close_fds=True)`로 넘기고, Popen 호출이 성공하면 부모 측 fd는 즉시 close해도
        자식은 OS 차원에서 자기 fd를 유지한다.

        같은 service_id로 또 호출되어도 그냥 새 fd를 반환한다 (정책 정보만 갱신).
        """
        path = self.log_path(service_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND: 자식이 여러 번 write해도 race 없이 끝에 추가됨
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(path, flags, 0o644)
        with self._lock:
            self._policies[service_id] = _RotatePolicy(max_bytes=max_bytes, keep=keep)
        return fd

    def stop_capture(self, service_id: str) -> None:
        """자식 종료 후 호출. 필요하면 회전을 수행한다.

        자식이 fd를 닫은 뒤(=자식 프로세스 종료 후) 호출하지 않으면 회전된 새 파일에
        자식이 안 쓰는 문제는 없지만, rename 시점에 자식이 살아있으면 자식은 여전히
        rename된 .log.1에 쓴다. ProcessManager는 자식의 wait이 반환된 뒤에 이 메서드를
        호출하도록 보장해야 한다.
        """
        with self._lock:
            policy = self._policies.pop(service_id, None)
        if policy is None:
            return
        self.rotate_if_needed(
            service_id,
            max_bytes=policy.max_bytes,
            keep=policy.keep,
        )

    def rotate_if_needed(self, service_id: str, *, max_bytes: int, keep: int) -> None:
        """Rotate a stopped service log using an explicit config policy.

        PM2 owns the child file descriptor, so it cannot use ``open_for_child``.
        Its adapter calls this only after PM2 confirms the service is stopped (or
        immediately before a stopped service starts), which preserves the same
        inode/SSE safety rule as the native process manager.
        """

        with self._lock:
            self._rotate_if_needed(
                service_id,
                _RotatePolicy(max_bytes=max_bytes, keep=keep),
            )

    # ------------------------------------------------------------------
    # 회전
    # ------------------------------------------------------------------

    def _rotate_if_needed(self, service_id: str, policy: "_RotatePolicy") -> None:
        if policy.max_bytes <= 0:
            return
        base = self.log_path(service_id)
        if not base.is_file():
            return
        try:
            size = base.stat().st_size
        except OSError:
            return
        if size < policy.max_bytes:
            return

        # 가장 오래된 것부터 한 칸씩 밀고 가장 오래된 건 삭제.
        if policy.keep > 0:
            oldest = self.rotated_path(service_id, policy.keep)
            if oldest.exists():
                try:
                    oldest.unlink()
                except OSError:
                    pass
            for n in range(policy.keep - 1, 0, -1):
                src = self.rotated_path(service_id, n)
                if src.exists():
                    try:
                        src.rename(self.rotated_path(service_id, n + 1))
                    except OSError:
                        pass
            try:
                base.rename(self.rotated_path(service_id, 1))
            except OSError:
                pass
        else:
            # keep == 0: 그냥 비운다
            try:
                base.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 읽기 (tail / streaming)
    # ------------------------------------------------------------------

    def tail(self, service_id: str, lines: int) -> tuple[list[str], int]:
        path = self.log_path(service_id)
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

    def read_since(self, service_id: str, offset: int) -> tuple[list[str], int]:
        path = self.log_path(service_id)
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
        lines = text.splitlines()
        return lines, size

    def stream(
        self,
        service_id: str,
        *,
        from_start: bool = False,
        idle_sleep: float = 0.5,
        keepalive_after: float = 15.0,
        stop_event=None,
    ) -> Iterator[str | None]:
        """지속적으로 새 줄을 yield하는 generator.

        새 데이터가 없는 시간이 keepalive_after를 넘으면 None을 yield하여
        호출자(SSE 핸들러)가 keepalive comment를 보낼 수 있게 한다.
        """
        path = self.log_path(service_id)
        while not path.is_file():
            if stop_event is not None and stop_event.is_set():
                return
            time.sleep(idle_sleep)

        offset = 0 if from_start else path.stat().st_size
        last_data_at = time.time()

        while True:
            if stop_event is not None and stop_event.is_set():
                return
            lines, offset = self.read_since(service_id, offset)
            if lines:
                last_data_at = time.time()
                for line in lines:
                    yield line
            else:
                if time.time() - last_data_at > keepalive_after:
                    last_data_at = time.time()
                    yield None
                time.sleep(idle_sleep)

    # ------------------------------------------------------------------
    # 정리
    # ------------------------------------------------------------------

    def close_all(self) -> None:
        with self._lock:
            self._policies.clear()


class _RotatePolicy:
    __slots__ = ("max_bytes", "keep")

    def __init__(self, *, max_bytes: int, keep: int) -> None:
        self.max_bytes = max_bytes
        self.keep = keep
