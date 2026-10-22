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

* 기본은 자식 종료 시 크기 기반 회전이다. 단순 rename은 열린 fd를 바꾸지 않는다.
* PM2의 선택적 실행 중 회전은 wrapper 잠금 안에서 reloadLogs로 fd를 재개방하고
  daemon의 이전 fd가 닫힌 뒤에만 pending 파일을 보관/압축한다.
* SSE는 이전 fd를 drain하고 inode 교체를 따라간다.

브라우저에서 임의 파일을 읽지 못하도록, 외부에서 받은 service_id는 사용 전에
ServiceRegistry로 검증해야 한다. 이 모듈은 service_id가 이미 검증되었다고 가정한다.
"""

from __future__ import annotations

import gzip
import shutil
import json
import tempfile
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

        self._shift_archives(service_id, policy.keep)
        if policy.keep:
            base.replace(self.rotated_path(service_id, 1))
        else:
            base.unlink(missing_ok=True)

    def _shift_archives(self, service_id: str, keep: int) -> None:
        # A slot is shared by plain/gzip archives, including a policy change.
        for candidate in self._root.glob(f"{service_id}.log.*"):
            number = candidate.name.removeprefix(f"{service_id}.log.").removesuffix(".gz")
            if number.isdigit() and int(number) >= keep:
                candidate.unlink(missing_ok=True)
        for n in range(keep - 1, 0, -1):
            for suffix in ("", ".gz"):
                old = self._root / f"{service_id}.log.{n}{suffix}"
                if old.exists():
                    old.replace(self._root / f"{service_id}.log.{n + 1}{suffix}")

    def pending_path(self, service_id: str) -> Path:
        return self._root / f"{service_id}.log.pending"

    def handoff_path(self, service_id: str) -> Path:
        return self._root / f"{service_id}.log.pending.closed"

    def _generation(self, service_id: str):
        try:
            data = json.loads((self._root / f"{service_id}.log.generation").read_text())
            if type(data.get("sequence")) is int and type(data.get("inode")) is int:
                return data
        except (OSError, ValueError, AttributeError):
            pass
        return {"sequence": 0, "inode": None}

    def _write_generation(self, service_id: str) -> None:
        data = {"sequence": self._generation(service_id)["sequence"] + 1,
                "inode": self.log_path(service_id).stat().st_ino}
        fd, temporary = tempfile.mkstemp(prefix=".log-generation-", dir=self._root)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream)
            os.replace(temporary, self._root / f"{service_id}.log.generation")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def mark_live_handoff(self, service_id: str) -> None:
        # Only the PM2 helper calls this after checking that the daemon closed
        # the previous fd. Compression may fail without blocking new SSE data.
        self.handoff_path(service_id).touch(mode=0o600)

    def needs_live_rotation(self, service_id: str, max_bytes: int) -> bool:
        try:
            return self.pending_path(service_id).exists() or (max_bytes > 0 and self.log_path(service_id).stat().st_size >= max_bytes)
        except OSError:
            return False

    def prepare_live_rotation(self, service_id: str, *, max_bytes: int) -> bool:
        with self._lock:
            if self.pending_path(service_id).exists():
                return True
            if not self.needs_live_rotation(service_id, max_bytes):
                return False
            self.handoff_path(service_id).unlink(missing_ok=True)
            self.log_path(service_id).rename(self.pending_path(service_id))
            # PM2 reopens with append; create now so readers see the new inode.
            self.log_path(service_id).touch(mode=0o600)
            self._write_generation(service_id)
            return True

    def finish_live_rotation(self, service_id: str, *, keep: int, compress: bool) -> None:
        with self._lock:
            pending = self.pending_path(service_id)
            if not pending.exists():
                return
            # Compression finishes before old archives are removed. On failure
            # leave the pending source for the next attempt.
            source = pending
            if compress and keep:
                temporary = Path(str(pending) + ".gz.tmp")
                with pending.open("rb") as src, gzip.open(temporary, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                source = temporary
            self._shift_archives(service_id, keep)
            if keep:
                source.replace(self._root / (f"{service_id}.log.1" + (".gz" if compress else "")))
            pending.unlink(missing_ok=True)
            self.handoff_path(service_id).unlink(missing_ok=True)

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

        # Keep the old fd until PM2 has flushed/closed it. A byte offset alone
        # cannot detect replacement when the new file is already larger.
        with path.open("rb") as initial:
            fh = initial
            if not from_start:
                fh.seek(0, io.SEEK_END)
            last_data_at = time.monotonic()
            generation = self._generation(service_id)
            last_generation = generation["sequence"] if generation["inode"] in {None, os.fstat(fh.fileno()).st_ino} else None
            try:
                while True:
                    if stop_event is not None and stop_event.is_set():
                        return
                    data = fh.read()
                    if data:
                        last_data_at = time.monotonic()
                        for line in data.decode("utf-8", errors="replace").splitlines():
                            yield line
                        continue
                    try:
                        current = path.stat()
                        previous = os.fstat(fh.fileno())
                        if current.st_ino != previous.st_ino:
                            if not self.pending_path(service_id).exists() or self.handoff_path(service_id).exists():
                                # One final drain after the writer closed.
                                data = fh.read()
                                if data:
                                    for line in data.decode("utf-8", errors="replace").splitlines():
                                        yield line
                                generation = self._generation(service_id)
                                if last_generation is None or generation["sequence"] != last_generation + 1:
                                    yield "[Control Server] 로그 회전 중 스트림 일부가 누락되었습니다. 보관 로그를 확인하세요."
                                last_generation = generation["sequence"]
                                new_fh = path.open("rb")
                                fh.close()
                                fh = new_fh
                                continue
                        elif current.st_size < fh.tell():
                            fh.seek(0)
                            continue
                    except FileNotFoundError:
                        pass
                    if time.monotonic() - last_data_at > keepalive_after:
                        last_data_at = time.monotonic()
                        yield None
                    time.sleep(idle_sleep)
            finally:
                fh.close()

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
