"""log_manager 단위 테스트.

v1.2.3 모델: 자식이 fd로 직접 stdout/stderr를 redirect한다.
LogManager는 fd를 만들어 주고, 자식 종료 후 stop_capture에서 회전을 결정한다.
"""
from __future__ import annotations

import os
from pathlib import Path

from log_manager import LogManager


def _write_via_child_fd(fd: int, data: bytes) -> None:
    """자식이 stdout으로 쓰는 동작을 흉내내기 위한 헬퍼."""
    os.write(fd, data)


def test_open_for_child_writes_lines(tmp_path: Path) -> None:
    """자식 fd로 쓴 내용이 .log 파일에 그대로 남는다."""
    lm = LogManager(tmp_path)
    fd = lm.open_for_child("svc", max_bytes=0, keep=0)
    try:
        _write_via_child_fd(fd, b"first\n")
        _write_via_child_fd(fd, b"second\n")
    finally:
        os.close(fd)
    # 자식이 종료된 시점에 호출.
    lm.stop_capture("svc")

    log = (tmp_path / "svc.log").read_text(encoding="utf-8")
    assert log == "first\nsecond\n"


def test_rotation_happens_on_stop_when_max_bytes_exceeded(tmp_path: Path) -> None:
    """max_bytes를 넘은 채로 자식이 종료되면 stop_capture에서 회전이 일어난다."""
    lm = LogManager(tmp_path)
    fd = lm.open_for_child("svc", max_bytes=40, keep=2)
    try:
        for line in [b"AAAAAAAAAAAAAAAAAA\n", b"BBBBBBBBBBBBBBBBBB\n", b"CCCCCCCCCCCCCCCCCC\n"]:
            _write_via_child_fd(fd, line)
    finally:
        os.close(fd)
    lm.stop_capture("svc")

    base = tmp_path / "svc.log"
    rot1 = tmp_path / "svc.log.1"
    # 자식이 살아있는 동안엔 회전 없으므로 stop 시점에 한 번 회전:
    # base는 비어있고 rot1에 모든 데이터가 들어있다.
    assert rot1.is_file()
    assert not base.is_file()
    text = rot1.read_text(encoding="utf-8")
    assert "AAAAAAAAAAAAAAAAAA" in text
    assert "BBBBBBBBBBBBBBBBBB" in text
    assert "CCCCCCCCCCCCCCCCCC" in text


def test_rotation_does_not_happen_when_under_max_bytes(tmp_path: Path) -> None:
    """max_bytes 이하면 회전 없음 (자식이 살아있는 동안과 동일하게 base에 누적)."""
    lm = LogManager(tmp_path)
    fd = lm.open_for_child("svc", max_bytes=1024, keep=3)
    try:
        _write_via_child_fd(fd, b"short\n")
    finally:
        os.close(fd)
    lm.stop_capture("svc")

    base = tmp_path / "svc.log"
    assert base.read_text(encoding="utf-8") == "short\n"
    assert not (tmp_path / "svc.log.1").exists()


def test_rotation_keep_caps_old_files(tmp_path: Path) -> None:
    """keep=2면 .1/.2까지만 남고 .3은 만들어지지 않는다.

    자식이 살아있는 동안엔 회전이 안 일어나므로, 회전을 여러 번 일으키려면
    여러 번의 start/stop 사이클을 흉내낸다.
    """
    lm = LogManager(tmp_path)
    for cycle in range(4):
        fd = lm.open_for_child("svc", max_bytes=20, keep=2)
        try:
            _write_via_child_fd(fd, ("X" * 30 + f"-{cycle}\n").encode())
        finally:
            os.close(fd)
        lm.stop_capture("svc")

    assert not (tmp_path / "svc.log.3").exists()
    # .1, .2는 존재, base는 비어있거나 마지막 회전 이후 비어있음.
    assert (tmp_path / "svc.log.1").is_file()
    assert (tmp_path / "svc.log.2").is_file()


def test_open_for_child_appends_to_existing(tmp_path: Path) -> None:
    """이전 .log가 있으면 그 끝에 append된다 (회전 안 일어난 상태에서 재시작)."""
    base = tmp_path / "svc.log"
    base.write_text("prev\n", encoding="utf-8")

    lm = LogManager(tmp_path)
    fd = lm.open_for_child("svc", max_bytes=0, keep=0)
    try:
        _write_via_child_fd(fd, b"new\n")
    finally:
        os.close(fd)
    lm.stop_capture("svc")

    assert base.read_text(encoding="utf-8") == "prev\nnew\n"


def test_tail_returns_last_n_lines(tmp_path: Path) -> None:
    lm = LogManager(tmp_path)
    p = tmp_path / "svc.log"
    p.write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\n", encoding="utf-8")

    lines, offset = lm.tail("svc", 3)
    assert lines == ["line 8", "line 9", "line 10"]
    assert offset == p.stat().st_size


def test_tail_zero_lines(tmp_path: Path) -> None:
    lm = LogManager(tmp_path)
    p = tmp_path / "svc.log"
    p.write_text("line\n", encoding="utf-8")

    lines, offset = lm.tail("svc", 0)
    assert lines == []
    assert offset == 0


def test_tail_missing_file(tmp_path: Path) -> None:
    lm = LogManager(tmp_path)
    lines, offset = lm.tail("svc", 5)
    assert lines == []
    assert offset == 0


def test_read_since(tmp_path: Path) -> None:
    lm = LogManager(tmp_path)
    p = tmp_path / "svc.log"
    p.write_text("a\nb\n", encoding="utf-8")
    lines, offset = lm.read_since("svc", 0)
    assert lines == ["a", "b"]

    with p.open("a", encoding="utf-8") as fh:
        fh.write("c\n")
    new_lines, new_offset = lm.read_since("svc", offset)
    assert new_lines == ["c"]
    assert new_offset > offset


def test_read_since_after_truncate(tmp_path: Path) -> None:
    lm = LogManager(tmp_path)
    p = tmp_path / "svc.log"
    p.write_text("aaaa\nbbbb\n", encoding="utf-8")
    _, offset = lm.read_since("svc", 0)

    p.write_text("z\n", encoding="utf-8")
    lines, new_offset = lm.read_since("svc", offset)
    assert lines == ["z"]
    assert new_offset == p.stat().st_size

