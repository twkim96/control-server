"""Lock an inherited open-file description; the parent keeps it locked after exit.

Used on macOS versions without /usr/bin/lockf. Never unlink the lock file.
"""
from __future__ import annotations

import errno
import fcntl
import sys
import time


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


if __name__ == "__main__":
    try:
        sys.exit(acquire(int(sys.argv[1]), float(sys.argv[2])))
    except (OSError, ValueError, IndexError):
        print("Control Server file lock unavailable", file=sys.stderr)
        sys.exit(74)
