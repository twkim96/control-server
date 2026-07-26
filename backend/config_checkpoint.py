"""Private last-known-good config checkpoint for PM2 lifecycle safety."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from config_loader import AppConfig, load_config


class ConfigCheckpointError(RuntimeError):
    """A private config checkpoint could not be saved or restored."""


class ConfigCheckpoint:
    def __init__(self, runtime_dir: str | os.PathLike[str]) -> None:
        self.path = Path(runtime_dir) / "last_good_config.yml"
        self._lock = threading.Lock()

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> AppConfig:
        if not self.exists():
            raise ConfigCheckpointError("last-good config checkpoint가 없습니다.")
        return load_config(self.path)

    def save_from(self, source: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        try:
            data = source_path.read_bytes()
        except OSError as exc:
            raise ConfigCheckpointError(f"config checkpoint 읽기 실패: {exc}") from exc
        with self._lock:
            self._atomic_write(self.path, data)

    def restore_to(self, target: str | os.PathLike[str]) -> None:
        with self._lock:
            try:
                data = self.path.read_bytes()
            except OSError as exc:
                raise ConfigCheckpointError(f"last-good config 읽기 실패: {exc}") from exc
            self._atomic_write(Path(target), data)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            os.chmod(path, 0o600)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ConfigCheckpointError(f"config checkpoint 쓰기 실패: {exc}") from exc


__all__ = ["ConfigCheckpoint", "ConfigCheckpointError"]
