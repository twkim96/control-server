from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import psutil

import resource_sampler as resource_sampler_module
from process_manager import RuntimeState
from resource_sampler import ResourceSampler


def test_sample_not_running() -> None:
    sampler = ResourceSampler()
    sample = sampler.sample("svc", RuntimeState())
    assert sample.available is False
    assert sample.reason == "not_running"
    assert sample.pid is None


def test_sample_current_process_and_cache() -> None:
    proc = psutil.Process(os.getpid())
    ticks = [100.0]
    sampler = ResourceSampler(ttl_seconds=5.0, clock=lambda: ticks[0])
    state = RuntimeState(pid=proc.pid, create_time=proc.create_time())

    first = sampler.sample("self", state)
    assert first.available is True
    assert first.reason == "ok"
    assert first.memory_rss_bytes is not None and first.memory_rss_bytes > 0
    assert first.process_count >= 1
    assert first.cpu_percent is None

    ticks[0] += 1.0
    second = sampler.sample("self", state)
    assert second is first


def test_sample_reports_cpu_after_ttl() -> None:
    proc = psutil.Process(os.getpid())
    ticks = [100.0]
    sampler = ResourceSampler(ttl_seconds=1.0, clock=lambda: ticks[0])
    state = RuntimeState(pid=proc.pid, create_time=proc.create_time())

    sampler.sample("self", state)
    # Do a tiny amount of work so process CPU time can move on fast machines.
    deadline = time.time() + 0.05
    while time.time() < deadline:
        pass
    ticks[0] += 2.0
    second = sampler.sample("self", state)
    assert second.available is True
    assert second.cpu_percent is not None
    assert second.cpu_percent >= 0


def test_sample_can_exclude_children_and_keeps_cache_modes_separate(monkeypatch) -> None:
    root = MagicMock()
    root.create_time.return_value = 10.0
    root.children.return_value = []
    root.cpu_times.return_value = SimpleNamespace(user=1.0, system=0.5)
    root.memory_info.return_value = SimpleNamespace(rss=1024)
    monkeypatch.setattr(resource_sampler_module.psutil, "Process", lambda _pid: root)
    monkeypatch.setattr(resource_sampler_module, "_darwin_recursive_child_pids", lambda _pid: None)

    sampler = ResourceSampler()
    state = RuntimeState(pid=123, create_time=10.0)

    controller = sampler.sample("same", state, include_children=False)
    service = sampler.sample("same", state, include_children=True)

    assert controller.process_count == 1
    root.children.assert_called_once_with(recursive=True)
    assert service is not controller


def test_sample_uses_darwin_child_pid_fast_path(monkeypatch) -> None:
    root = MagicMock()
    child = MagicMock()
    for proc, rss in ((root, 1024), (child, 2048)):
        proc.create_time.return_value = 10.0
        proc.cpu_times.return_value = SimpleNamespace(user=1.0, system=0.5)
        proc.memory_info.return_value = SimpleNamespace(rss=rss)

    processes = {123: root, 456: child}
    monkeypatch.setattr(
        resource_sampler_module.psutil,
        "Process",
        lambda pid: processes[pid],
    )
    monkeypatch.setattr(
        resource_sampler_module,
        "_darwin_recursive_child_pids",
        lambda _pid: [456],
    )

    sample = ResourceSampler().sample(
        "svc",
        RuntimeState(pid=123, create_time=10.0),
    )

    assert sample.process_count == 2
    assert sample.children_count == 1
    assert sample.memory_rss_bytes == 3072
    root.children.assert_not_called()


def test_sample_rejects_pid_reuse() -> None:
    proc = psutil.Process(os.getpid())
    sampler = ResourceSampler()
    state = RuntimeState(pid=proc.pid, create_time=proc.create_time() - 1000.0)

    sample = sampler.sample("self", state)
    assert sample.available is False
    assert sample.reason == "pid_reused"


def test_sample_accepts_subsecond_create_time_drift() -> None:
    proc = psutil.Process(os.getpid())
    sampler = ResourceSampler()
    state = RuntimeState(pid=proc.pid, create_time=proc.create_time() - 0.5)

    sample = sampler.sample("self", state)
    assert sample.available is True
    assert sample.reason == "ok"


def test_sample_handles_no_such_process(monkeypatch) -> None:
    def fake_process(pid: int):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(resource_sampler_module.psutil, "Process", fake_process)
    sample = ResourceSampler().sample("missing", RuntimeState(pid=12345))
    assert sample.available is False
    assert sample.reason == "no_such_process"


def test_sample_handles_access_denied(monkeypatch) -> None:
    def fake_process(pid: int):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(resource_sampler_module.psutil, "Process", fake_process)
    sample = ResourceSampler().sample("denied", RuntimeState(pid=12345))
    assert sample.available is False
    assert sample.reason == "access_denied"
