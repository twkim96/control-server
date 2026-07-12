"""시스템에 설치된 Python 인터프리터를 찾아 목록으로 반환한다.

ActionItem이나 ServiceConfig를 등록할 때 `python` / `python3`를 매번 절대경로로
적는 마찰을 줄이려는 도우미. 호출자(라우트)는 결과를 그대로 UI에 내려주고, UI는
선택 결과를 command[0]으로 박아 넣는다.

탐색 위치 (사용자 환경에 맞춰 합리적으로):
- `$PATH`의 `python`, `python3`, `python3.X` (basename + glob)
- macOS 시스템: `/usr/bin/python3`
- Homebrew (Apple Silicon): `/opt/homebrew/bin/python3*`
- Homebrew (Intel) / 기타: `/usr/local/bin/python3*`
- Anaconda / Miniconda: `/opt/anaconda3/bin/python3*`, `/opt/miniconda3/bin/python3*`,
  `~/anaconda3/bin/python3*`, `~/miniconda3/bin/python3*`
- pyenv: `~/.pyenv/versions/*/bin/python`, `~/.pyenv/shims/python*`

shim은 launchd 환경에서 의도와 다른 인터프리터를 잡을 수 있어 별도 note를 남긴다.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


# 후보 디렉터리. 존재 안 하면 그냥 건너뛴다.
_FIXED_DIRS: tuple[str, ...] = (
    "/usr/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/anaconda3/bin",
    "/opt/miniconda3/bin",
)
_HOME_DIRS: tuple[str, ...] = (
    "~/anaconda3/bin",
    "~/miniconda3/bin",
)
_PYENV_VERSIONS_GLOB = "~/.pyenv/versions/*/bin/python"
_PYENV_SHIMS_GLOB = "~/.pyenv/shims/python*"


# basename 매칭: python, python3, python3.10, python3.14 등.
_BASENAME_RE = re.compile(r"^python(?:\d+(?:\.\d+)?)?$")


@dataclass(frozen=True)
class PythonInterpreter:
    path: str
    version: str  # "3.14.5" 또는 "?" (조회 실패 시)
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "version": self.version, "note": self.note}


def list_python_interpreters() -> list[PythonInterpreter]:
    """시스템의 python 인터프리터 후보를 모아 정렬된 리스트로 반환한다.

    같은 실제 바이너리(`os.path.realpath`)는 한 번만 포함된다.
    """
    candidates: dict[str, str] = {}  # realpath -> 표시용 path
    notes: dict[str, str] = {}       # realpath -> note

    def add(path: str, *, note: str = "") -> None:
        if not path:
            return
        try:
            real = os.path.realpath(path)
        except OSError:
            return
        if not _looks_executable(real):
            return
        # 첫 등록만 표시 path로 사용. 이후 같은 real이 들어오면 무시.
        if real not in candidates:
            candidates[real] = path
            if note:
                notes[real] = note

    for directory in _resolve_dirs(_FIXED_DIRS, _HOME_DIRS):
        for entry in _scan_directory(directory):
            add(entry)

    # PATH 룩업 (which 류). PATH의 동일 entry는 위 _FIXED_DIRS와 중복될 수 있지만
    # realpath 기반 dedup으로 자연스레 정리됨.
    # pyenv shim 디렉터리가 PATH에 들어있는 경우가 흔하므로 자동으로 hint를 붙인다.
    for directory in _path_dirs():
        is_shim_dir = "pyenv/shims" in str(directory)
        note = "pyenv shim — launchd 환경에선 의도와 다를 수 있음" if is_shim_dir else ""
        for entry in _scan_directory(directory):
            add(entry, note=note)

    # pyenv versions 디렉터리 — 진짜 인터프리터.
    for entry in _glob_home(_PYENV_VERSIONS_GLOB):
        add(entry, note="pyenv version")

    # pyenv shim glob — 위 PATH 스캔에서 못 잡았을 때 안전망.
    for entry in _glob_home(_PYENV_SHIMS_GLOB):
        add(entry, note="pyenv shim — launchd 환경에선 의도와 다를 수 있음")

    interpreters: list[PythonInterpreter] = []
    for real, display in candidates.items():
        version = _detect_version(real)
        if version is None:
            continue
        interpreters.append(
            PythonInterpreter(
                path=display,
                version=version,
                note=notes.get(real, ""),
            )
        )

    return _sort_interpreters(interpreters)


# ----------------------------------------------------------------------
# 내부 유틸
# ----------------------------------------------------------------------


def _resolve_dirs(fixed: tuple[str, ...], home_relative: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for d in fixed:
        p = Path(d)
        if p.is_dir():
            out.append(p)
    for d in home_relative:
        p = Path(d).expanduser()
        if p.is_dir():
            out.append(p)
    return out


def _path_dirs() -> list[Path]:
    raw = os.environ.get("PATH", "")
    out: list[Path] = []
    seen: set[Path] = set()
    for chunk in raw.split(os.pathsep):
        if not chunk:
            continue
        try:
            p = Path(chunk)
        except (OSError, ValueError):
            continue
        if p in seen:
            continue
        seen.add(p)
        if p.is_dir():
            out.append(p)
    return out


def _scan_directory(directory: Path) -> list[str]:
    out: list[str] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return out
    for entry in entries:
        if not _BASENAME_RE.match(entry.name):
            continue
        out.append(str(entry))
    return out


def _glob_home(pattern: str) -> list[str]:
    expanded = os.path.expanduser(pattern)
    base = Path(expanded.split("*", 1)[0]).parent
    if not base.exists():
        return []
    # Path.glob는 첫 *부터 처리. 패턴이 절대경로이므로 root에서 relative로 변환.
    try:
        from glob import glob

        return glob(expanded)
    except OSError:
        return []


def _looks_executable(path: str) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    if not (st.st_mode & 0o111):
        return False
    # 디렉터리는 제외
    return os.path.isfile(path)


_VERSION_RE = re.compile(r"Python\s+(\d+(?:\.\d+){1,2})")


def _detect_version(path: str) -> str | None:
    """`python -V`를 짧은 timeout으로 호출해서 버전을 얻는다.

    실패하면 None을 반환해 호출자가 entry 자체를 빼게 한다 (broken / 다른 바이너리 등).
    """
    try:
        result = subprocess.run(
            [path, "-V"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (result.stdout or "") + (result.stderr or "")
    m = _VERSION_RE.search(out)
    if not m:
        return None
    return m.group(1)


def _sort_interpreters(items: list[PythonInterpreter]) -> list[PythonInterpreter]:
    """정렬 규칙:

    1. shim(note에 'shim' 포함)은 맨 아래
    2. 그 외는 path 기준 알파벳 정렬
    """

    def key(item: PythonInterpreter) -> tuple[int, str]:
        is_shim = "shim" in item.note
        return (1 if is_shim else 0, item.path)

    return sorted(items, key=key)


__all__ = [
    "PythonInterpreter",
    "list_python_interpreters",
]
