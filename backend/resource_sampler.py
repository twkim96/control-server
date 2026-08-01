"""Lightweight per-service resource sampling."""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from threading import RLock
from typing import Any, Literal

import psutil

from process_manager import RuntimeState


def _load_darwin_child_pid_api() -> tuple[Any | None, Any | None]:
    if sys.platform != "darwin":
        return None, None
    try:
        library = ctypes.CDLL(ctypes.util.find_library("proc") or "libproc.dylib")
        function = library.proc_listchildpids
        function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        function.restype = ctypes.c_int
        return library, function
    except (AttributeError, OSError):
        return None, None


_DARWIN_LIBPROC, _DARWIN_LIST_CHILD_PIDS = _load_darwin_child_pid_api()


ResourceReason = Literal[
    "ok",
    "not_running",
    "pid_reused",
    "access_denied",
    "no_such_process",
]


@dataclass(frozen=True)
class ResourceSample:
    available: bool
    reason: ResourceReason
    pid: int | None
    sampled_at: float
    cpu_percent: float | None
    memory_rss_bytes: int | None
    process_count: int
    children_count: int
    partial: bool
    discovered_process_count: int
    sampled_process_count: int
    skipped_process_count: int
    window_seconds: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _CacheEntry:
    signature: tuple[int | None, float | None, bool]
    sampled_at_monotonic: float
    process_cpu_seconds: dict[tuple[int, float], float]
    sample: ResourceSample


class ResourceSampler:
    """Sample only tracked PIDs and reuse values inside a short TTL.

    The sampler deliberately avoids full process or port scans. For CPU it stores the
    previous CPU time per ``(pid, create_time)`` and computes a delta on the next
    real sample. The first sample can therefore report cpu_percent=None, which the
    UI renders as "—".
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 5.0,
        wall_clock=time.time,
        monotonic_clock=time.monotonic,
    ) -> None:
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._lock = RLock()
        self._cache: dict[str, _CacheEntry] = {}

    def sample(
        self,
        service_id: str,
        state: RuntimeState,
        *,
        include_children: bool = True,
    ) -> ResourceSample:
        wall_now = self._wall_clock()
        monotonic_now = self._monotonic_clock()
        signature = (state.pid, state.create_time, include_children)
        if state.pid is None:
            return self._unavailable("not_running", None, wall_now)

        with self._lock:
            cached = self._cache.get(service_id)
            if (
                cached is not None
                and cached.signature == signature
                and monotonic_now - cached.sampled_at_monotonic < self._ttl_seconds
            ):
                return cached.sample

            sample, process_cpu_seconds = self._sample_uncached(
                state,
                wall_now,
                monotonic_now,
                cached,
                include_children=include_children,
            )
            self._cache[service_id] = _CacheEntry(
                signature=signature,
                sampled_at_monotonic=monotonic_now,
                process_cpu_seconds=process_cpu_seconds,
                sample=sample,
            )
            return sample

    def forget(self, service_id: str) -> None:
        with self._lock:
            self._cache.pop(service_id, None)

    def prune(self, active_service_ids: Iterable[str]) -> None:
        active = set(active_service_ids)
        with self._lock:
            for service_id in tuple(self._cache):
                if service_id not in active:
                    self._cache.pop(service_id, None)

    def _sample_uncached(
        self,
        state: RuntimeState,
        wall_now: float,
        monotonic_now: float,
        cached: _CacheEntry | None,
        *,
        include_children: bool,
    ) -> tuple[ResourceSample, dict[tuple[int, float], float]]:
        assert state.pid is not None
        try:
            root = psutil.Process(state.pid)
        except psutil.NoSuchProcess:
            return self._unavailable("no_such_process", state.pid, wall_now), {}
        except psutil.ZombieProcess:
            return self._unavailable("no_such_process", state.pid, wall_now), {}
        except (psutil.AccessDenied, OSError):
            return self._unavailable("access_denied", state.pid, wall_now), {}

        if state.create_time is not None:
            try:
                if not _create_time_matches(root, state.create_time):
                    return self._unavailable("pid_reused", state.pid, wall_now), {}
            except psutil.NoSuchProcess:
                return self._unavailable("no_such_process", state.pid, wall_now), {}
            except psutil.ZombieProcess:
                return self._unavailable("no_such_process", state.pid, wall_now), {}
            except (psutil.AccessDenied, OSError):
                return self._unavailable("access_denied", state.pid, wall_now), {}

        processes = [root]
        discovered_process_count = 1
        partial = False
        if include_children:
            child_pids = _darwin_recursive_child_pids(root.pid)
            if child_pids is None:
                try:
                    children = root.children(recursive=True)
                except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError):
                    # Parent metrics are still useful, but the tree is incomplete.
                    children = []
                    partial = True
                processes.extend(children)
                discovered_process_count += len(children)
            else:
                discovered_process_count += len(child_pids)
                for pid in child_pids:
                    try:
                        processes.append(psutil.Process(pid))
                    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError):
                        partial = True
                        continue

        process_cpu_seconds: dict[tuple[int, float], float] = {}
        rss_bytes = 0
        sampled_count = 0
        children_count = 0
        root_sampled = False

        for index, proc in enumerate(processes):
            try:
                create_time = float(proc.create_time())
                times = proc.cpu_times()
                memory = proc.memory_info()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                if index == 0:
                    return self._unavailable("no_such_process", state.pid, wall_now), {}
                partial = True
                continue
            except (psutil.AccessDenied, OSError):
                if index == 0:
                    return self._unavailable("access_denied", state.pid, wall_now), {}
                partial = True
                continue

            process_cpu_seconds[(proc.pid, create_time)] = (
                float(times.user) + float(times.system)
            )
            rss_bytes += int(memory.rss)
            sampled_count += 1
            if index == 0:
                root_sampled = True
            else:
                children_count += 1

        if not root_sampled:
            return self._unavailable("no_such_process", state.pid, wall_now), {}

        skipped_process_count = max(0, discovered_process_count - sampled_count)
        partial = partial or skipped_process_count > 0
        cpu_percent: float | None = None
        window_seconds: float | None = None
        if cached is not None and cached.sample.available and cached.signature == (
            state.pid,
            state.create_time,
            include_children,
        ):
            elapsed = monotonic_now - cached.sampled_at_monotonic
            if elapsed > 0:
                cpu_delta = 0.0
                for identity, current_cpu_seconds in process_cpu_seconds.items():
                    previous_cpu_seconds = cached.process_cpu_seconds.get(identity)
                    if previous_cpu_seconds is not None:
                        cpu_delta += max(0.0, current_cpu_seconds - previous_cpu_seconds)
                        continue
                    # A child created after the previous wall sample belongs to this
                    # interval. An older process first discovered now establishes only
                    # a baseline, avoiding a lifetime-CPU spike.
                    if identity[1] >= cached.sample.sampled_at:
                        cpu_delta += max(0.0, current_cpu_seconds)
                window_seconds = elapsed
                cpu_percent = round((cpu_delta / elapsed) * 100.0, 1)

        return (
            ResourceSample(
                available=True,
                reason="ok",
                pid=state.pid,
                sampled_at=wall_now,
                cpu_percent=cpu_percent,
                memory_rss_bytes=rss_bytes,
                process_count=sampled_count,
                children_count=children_count,
                partial=partial,
                discovered_process_count=discovered_process_count,
                sampled_process_count=sampled_count,
                skipped_process_count=skipped_process_count,
                window_seconds=window_seconds,
            ),
            process_cpu_seconds,
        )

    def _unavailable(
        self,
        reason: ResourceReason,
        pid: int | None,
        now: float,
    ) -> ResourceSample:
        return ResourceSample(
            available=False,
            reason=reason,
            pid=pid,
            sampled_at=now,
            cpu_percent=None,
            memory_rss_bytes=None,
            process_count=0,
            children_count=0,
            partial=False,
            discovered_process_count=0,
            sampled_process_count=0,
            skipped_process_count=0,
            window_seconds=None,
        )


def _create_time_matches(proc: psutil.Process, expected: float) -> bool:
    return abs(float(proc.create_time()) - float(expected)) <= 1.0


def _darwin_recursive_child_pids(root_pid: int) -> list[int] | None:
    """Return descendants without psutil's macOS-wide PID/PPID scan.

    proc_listchildpids is a direct kernel query for one parent. Returning None
    means the API is unavailable or failed, so callers can use psutil as fallback.
    """
    if _DARWIN_LIST_CHILD_PIDS is None:
        return None

    descendants: list[int] = []
    seen = {root_pid}
    stack = [root_pid]
    while stack:
        parent_pid = stack.pop()
        direct = _darwin_direct_child_pids(parent_pid)
        if direct is None:
            return None
        for pid in direct:
            if pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            descendants.append(pid)
            stack.append(pid)
    return descendants


def _darwin_direct_child_pids(parent_pid: int) -> list[int] | None:
    assert _DARWIN_LIST_CHILD_PIDS is not None
    capacity = 16
    while capacity <= 4096:
        buffer = (ctypes.c_int * capacity)()
        count = _DARWIN_LIST_CHILD_PIDS(
            parent_pid,
            buffer,
            ctypes.sizeof(buffer),
        )
        if count < 0:
            return None
        if count < capacity:
            return [int(pid) for pid in buffer[:count] if pid > 0]
        capacity *= 2
    return None
