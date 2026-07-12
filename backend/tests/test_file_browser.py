"""file_browser 보안 + 동작 테스트."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from file_browser import FileBrowser, PathNotAllowedError


def test_lists_dir_within_root(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("b", encoding="utf-8")

    fb = FileBrowser([tmp_path])
    entries = fb.list_dir(tmp_path)
    names = [e.name for e in entries]
    assert "a.py" in names
    assert "sub" in names
    # 디렉터리 우선 정렬
    assert entries[0].is_dir is True


def test_rejects_outside_root(tmp_path: Path) -> None:
    inside = tmp_path / "inside"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    fb = FileBrowser([inside])
    with pytest.raises(PathNotAllowedError):
        fb.list_dir(outside)


def test_rejects_traversal_with_dotdot(tmp_path: Path) -> None:
    inside = tmp_path / "inside"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    fb = FileBrowser([inside])
    sneaky = inside / ".." / "outside"
    with pytest.raises(PathNotAllowedError):
        fb.list_dir(sneaky)


def test_rejects_symlink_pointing_outside(tmp_path: Path) -> None:
    inside = tmp_path / "inside"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope", encoding="utf-8")

    link = inside / "exit"
    os.symlink(outside, link)

    fb = FileBrowser([inside])
    with pytest.raises(PathNotAllowedError):
        fb.list_dir(link)


def test_missing_path_raises_filenotfound(tmp_path: Path) -> None:
    fb = FileBrowser([tmp_path])
    with pytest.raises(FileNotFoundError):
        fb.list_dir(tmp_path / "no_such")


def test_list_roots_returns_existing_roots(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    fb = FileBrowser([a, tmp_path / "missing"])
    roots = fb.list_roots()
    assert len(roots) == 1
    assert roots[0].path == str(a)


def test_is_allowed(tmp_path: Path) -> None:
    fb = FileBrowser([tmp_path])
    inside = tmp_path / "x"
    inside.mkdir()
    assert fb.is_allowed(inside) is True
    assert fb.is_allowed(tmp_path / ".." / "no") is False
