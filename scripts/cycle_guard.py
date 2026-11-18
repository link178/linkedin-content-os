"""Cross-process mutex: at most one editorial cycle pipeline at a time. Stdlib only."""

from __future__ import annotations

import os
import sys
from pathlib import Path

GLOBAL_LOCK_NAME = "cycle.lock"
LOCK_BYTE = 1
SKIPPED_OUTCOME = "SKIPPED_ALREADY_RUNNING"

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


def lock_path(content_root: Path) -> Path:
    return content_root / "cycles" / GLOBAL_LOCK_NAME


class CycleGuard:
    def __init__(self, content_root: Path) -> None:
        self.content_root = content_root
        self._handle = None
        self._locked = False

    def acquire(self) -> tuple[bool, str]:
        if self._locked:
            return True, ""
        path = lock_path(self.content_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(path, "a+b")
        try:
            if sys.platform == "win32":
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, LOCK_BYTE)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._close_handle()
            return False, "another editorial cycle is already running"
        self._locked = True
        try:
            self._handle.seek(0)
            self._handle.truncate()
            self._handle.write(str(os.getpid()).encode("ascii"))
            self._handle.flush()
        except OSError:
            self.release()
            return False, "could not record cycle lock owner"
        return True, ""

    def release(self) -> None:
        if not self._locked or self._handle is None:
            self._close_handle()
            return
        try:
            if sys.platform == "win32":
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, LOCK_BYTE)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._locked = False
        self._close_handle()

    def _close_handle(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None


def is_busy(content_root: Path) -> bool:
    """Return True when another process holds the global cycle lock."""
    guard = CycleGuard(content_root)
    ok, _ = guard.acquire()
    if ok:
        guard.release()
        return False
    return True