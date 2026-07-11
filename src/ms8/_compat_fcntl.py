"""Minimal Windows compatibility for the subset of :mod:`fcntl` used by MS8.

This module is registered as ``fcntl`` only on Windows, where CPython does not
provide the POSIX module. It intentionally implements only ``flock`` and the
lock constants required by the self-check runner.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

try:  # pragma: no cover - available only on Windows
    msvcrt: Any = importlib.import_module("msvcrt")
except ModuleNotFoundError:  # pragma: no cover - imported only by Windows bootstrap
    msvcrt = None

LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


def _prepare_lock_byte(fd: int) -> int:
    """Ensure the file has one lockable byte and position the descriptor at it."""
    position = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, 0, os.SEEK_SET)
    if os.fstat(fd).st_size == 0:
        os.write(fd, b"\0")
        os.fsync(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    return position


def flock(fd: int, operation: int) -> None:
    """Apply or release a one-byte Windows file lock.

    ``check_runner`` uses only exclusive, non-blocking and unlock operations.
    A failed non-blocking acquisition is normalized to ``BlockingIOError`` so
    the existing concurrent-run handling remains platform-independent.
    """
    if msvcrt is None:  # pragma: no cover - defensive misuse guard
        raise OSError("Windows locking support is unavailable")

    original_position = _prepare_lock_byte(fd)
    try:
        if operation & LOCK_UN:
            mode = msvcrt.LK_UNLCK
        elif operation & LOCK_NB:
            mode = msvcrt.LK_NBLCK
        else:
            mode = msvcrt.LK_LOCK

        try:
            msvcrt.locking(fd, mode, 1)
        except OSError as exc:
            if operation & LOCK_NB:
                raise BlockingIOError(exc.errno, str(exc)) from exc
            raise
    finally:
        try:
            os.lseek(fd, original_position, os.SEEK_SET)
        except OSError:
            pass
