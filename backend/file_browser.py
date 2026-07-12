"""제한된 파일/폴더 탐색.

서버 등록 화면에서 cwd, entry_file 같은 경로를 고를 때 사용한다.
controller.allowed_path_roots 안쪽만 노출하며, symlink로 밖을 가리키는 경로는 거부한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class PathNotAllowedError(PermissionError):
    """허용 루트 밖의 경로를 요청했을 때 발생."""


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    is_dir: bool
    size: int | None  # 디렉터리면 None
    mtime: float | None


class FileBrowser:
    def __init__(self, allowed_roots: Iterable[str | os.PathLike[str]]) -> None:
        # symlink resolve 후 비교하기 위해 realpath 기반으로 보관
        self._roots: tuple[Path, ...] = tuple(
            Path(r).expanduser().resolve(strict=False) for r in allowed_roots
        )
        if not self._roots:
            raise ValueError("FileBrowser는 최소 한 개 이상의 allowed_root가 필요합니다.")

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    def list_roots(self) -> list[FileEntry]:
        """등록된 모든 root를 디렉터리 엔트리 형태로 반환."""
        entries: list[FileEntry] = []
        for root in self._roots:
            if not root.exists():
                continue
            stat = root.stat()
            entries.append(
                FileEntry(
                    name=str(root),
                    path=str(root),
                    is_dir=root.is_dir(),
                    size=None if root.is_dir() else stat.st_size,
                    mtime=stat.st_mtime,
                )
            )
        return entries

    def list_dir(self, target: str | os.PathLike[str]) -> list[FileEntry]:
        path = self._validate(target)
        if not path.is_dir():
            raise NotADirectoryError(f"디렉터리가 아닙니다: {path}")

        entries: list[FileEntry] = []
        for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                stat = child.stat()
            except OSError:
                continue
            entries.append(
                FileEntry(
                    name=child.name,
                    path=str(child),
                    is_dir=child.is_dir(),
                    size=None if child.is_dir() else stat.st_size,
                    mtime=stat.st_mtime,
                )
            )
        return entries

    def is_allowed(self, target: str | os.PathLike[str]) -> bool:
        try:
            self._validate(target)
        except (PathNotAllowedError, FileNotFoundError):
            return False
        return True

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    def _validate(self, target: str | os.PathLike[str]) -> Path:
        candidate = Path(target).expanduser()
        # symlink를 따라가도 허용 루트 안에 있어야 한다
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"존재하지 않는 경로: {candidate}") from exc

        for root in self._roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise PathNotAllowedError(f"허용 루트 밖의 경로: {candidate}")


def entry_to_dict(entry: FileEntry) -> dict[str, object]:
    return {
        "name": entry.name,
        "path": entry.path,
        "is_dir": entry.is_dir,
        "size": entry.size,
        "mtime": entry.mtime,
    }


__all__ = [
    "FileBrowser",
    "FileEntry",
    "PathNotAllowedError",
    "entry_to_dict",
]
