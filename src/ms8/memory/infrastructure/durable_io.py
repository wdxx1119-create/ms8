"""Cross-platform durable file primitives for ledger and projection artifacts.

Windows can temporarily deny ``os.replace`` while antivirus, indexing, or another
process still holds a file handle. These helpers keep writes atomic while applying
a bounded retry policy only for retryable Windows sharing violations.
"""

from __future__ import annotations

import errno
import importlib
import os
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

_IS_WINDOWS = os.name == "nt"
_RETRYABLE_WINDOWS_ERRNOS = {
    errno.EACCES,
    errno.EPERM,
    errno.EBUSY,
}
_GLOBAL_LOCK = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


class FileLockTimeoutError(TimeoutError):
    """Raised when a cross-process file lock cannot be acquired in time."""


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _GLOBAL_LOCK:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


def fsync_directory(path: Path) -> None:
    """Best-effort directory fsync.

    Windows does not support opening directories with the POSIX flags used here,
    so failure is intentionally non-fatal after the file itself has been fsynced.
    """

    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        os.close(descriptor)


def replace_path(
    source: Path,
    destination: Path,
    *,
    attempts: int = 20,
    initial_delay: float = 0.01,
    max_delay: float = 0.25,
) -> None:
    """Atomically replace ``destination`` with bounded Windows retries."""

    if attempts < 1:
        raise ValueError("attempts must be positive")
    delay = max(0.0, initial_delay)
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            retryable = _IS_WINDOWS and (
                isinstance(exc, PermissionError) or exc.errno in _RETRYABLE_WINDOWS_ERRNOS
            )
            if not retryable or attempt + 1 >= attempts:
                raise
            time.sleep(delay)
            delay = min(max_delay, max(initial_delay, delay * 2 if delay else initial_delay))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes durably through a same-directory temporary file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_path(temporary, target)
        fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path, payload: Mapping[str, Any], *, serializer: Any) -> None:
    """Write one canonical JSON object using an injected serializer."""

    atomic_write_text(path, serializer(payload) + "\n")


def _prepare_lock_file(handle: Any) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)


def _try_lock(handle: Any) -> None:
    if _IS_WINDOWS:
        msvcrt: Any = importlib.import_module("msvcrt")

        _prepare_lock_file(handle)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    fcntl: Any = importlib.import_module("fcntl")

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: Any) -> None:
    if _IS_WINDOWS:
        msvcrt: Any = importlib.import_module("msvcrt")

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    fcntl: Any = importlib.import_module("fcntl")

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_file_lock(
    path: Path,
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.05,
) -> Iterator[None]:
    """Acquire a process- and thread-safe exclusive lock with a bounded wait."""

    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    lock_path = Path(path)
    local_lock = _thread_lock(lock_path)
    with local_lock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    _try_lock(handle)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise FileLockTimeoutError(f"timed out acquiring lock: {lock_path}") from exc
                    time.sleep(poll_interval)
            try:
                yield
            finally:
                _unlock(handle)


__all__ = [
    "FileLockTimeoutError",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "exclusive_file_lock",
    "fsync_directory",
    "replace_path",
]
