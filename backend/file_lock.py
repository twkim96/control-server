"""Lock an inherited open-file description; the parent keeps it locked after exit.

Used on macOS versions without /usr/bin/lockf. Never unlink the lock file.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import sys
import time
from contextlib import contextmanager


def acquire(fd: int, timeout: float) -> int:
    deadline = time.monotonic() + max(0, timeout)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return 0
        except OSError as exc:
            if exc.errno not in {errno.EAGAIN, errno.EACCES}:
                raise
            if time.monotonic() >= deadline:
                return 75
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))


@contextmanager
def managed_lock(home):
    """Same home lock as lib/transaction.mjs; excludes app/engine cutovers."""
    directory = Path('/private/tmp') / f'control-server-locks-{os.getuid()}'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError('unsafe managed lock directory')
    digest = hashlib.sha256(str(Path(home).resolve()).encode()).hexdigest()
    fd = os.open(directory / f'{digest}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise OSError('unsafe managed lock file')
        if acquire(fd, 0):
            raise OSError('managed operation busy')
        yield
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        sys.exit(acquire(int(sys.argv[1]), float(sys.argv[2])))
    except (OSError, ValueError, IndexError):
        print("Control Server file lock unavailable", file=sys.stderr)
        sys.exit(74)
