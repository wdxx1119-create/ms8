from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ms8.memory.infrastructure import durable_io
from ms8.memory.infrastructure.durable_io import (
    FileLockTimeoutError,
    atomic_write_bytes,
    exclusive_file_lock,
    replace_path,
)


def test_atomic_write_supports_unicode_and_space_paths(tmp_path: Path) -> None:
    target = tmp_path / "Windows 验收 空格" / "ledger manifest.json"

    atomic_write_bytes(target, b'{"ok":true}\n')

    assert target.read_bytes() == b'{"ok":true}\n'
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_replace_path_retries_transient_windows_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    original_replace = os.replace
    calls = 0

    def flaky_replace(first: os.PathLike[str] | str, second: os.PathLike[str] | str) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError("simulated Windows sharing violation")
        original_replace(first, second)

    monkeypatch.setattr(durable_io, "_IS_WINDOWS", True)
    monkeypatch.setattr(durable_io.os, "replace", flaky_replace)
    monkeypatch.setattr(durable_io.time, "sleep", lambda _seconds: None)

    replace_path(source, destination, attempts=4)

    assert calls == 3
    assert destination.read_bytes() == b"new"
    assert not source.exists()


def test_replace_path_does_not_retry_non_windows_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_bytes(b"new")
    calls = 0

    def denied_replace(_first: object, _second: object) -> None:
        nonlocal calls
        calls += 1
        raise PermissionError("denied")

    monkeypatch.setattr(durable_io, "_IS_WINDOWS", False)
    monkeypatch.setattr(durable_io.os, "replace", denied_replace)

    with pytest.raises(PermissionError):
        replace_path(source, destination, attempts=5)

    assert calls == 1


def test_exclusive_file_lock_serializes_threads(tmp_path: Path) -> None:
    lock_path = tmp_path / "state" / ".ledger.lock"
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_worker() -> None:
        with exclusive_file_lock(lock_path, timeout=2.0, poll_interval=0.01):
            first_entered.set()
            release_first.wait(timeout=2.0)

    def second_worker() -> None:
        first_entered.wait(timeout=2.0)
        with exclusive_file_lock(lock_path, timeout=2.0, poll_interval=0.01):
            second_entered.set()

    first = threading.Thread(target=first_worker)
    second = threading.Thread(target=second_worker)
    first.start()
    second.start()
    assert first_entered.wait(timeout=1.0)
    time.sleep(0.05)
    assert not second_entered.is_set()
    release_first.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert second_entered.is_set()


def test_exclusive_file_lock_reports_bounded_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / ".timeout.lock"

    def always_busy(_handle: object) -> None:
        raise PermissionError("busy")

    timeline = iter((0.0, 0.0, 1.0, 1.0))
    monkeypatch.setattr(durable_io, "_try_lock", always_busy)
    monkeypatch.setattr(durable_io.time, "monotonic", lambda: next(timeline))
    monkeypatch.setattr(durable_io.time, "sleep", lambda _seconds: None)

    with pytest.raises(FileLockTimeoutError):
        with exclusive_file_lock(lock_path, timeout=0.5, poll_interval=0.01):
            raise AssertionError("lock body must not run")


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cross-process locking")
def test_windows_exclusive_file_lock_blocks_a_second_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "进程 锁" / ".ledger.lock"
    ready_path = tmp_path / "child-ready.txt"
    release_path = tmp_path / "child-release.txt"
    child_code = """
import sys
import time
from pathlib import Path
from ms8.memory.infrastructure.durable_io import exclusive_file_lock

lock_path = Path(sys.argv[1])
ready_path = Path(sys.argv[2])
release_path = Path(sys.argv[3])
with exclusive_file_lock(lock_path, timeout=5.0, poll_interval=0.01):
    ready_path.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 10.0
    while not release_path.exists():
        if time.monotonic() >= deadline:
            raise SystemExit("parent did not release child lock")
        time.sleep(0.02)
"""
    environment = os.environ.copy()
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(lock_path), str(ready_path), str(release_path)],
        env=environment,
    )
    try:
        deadline = time.monotonic() + 5.0
        while not ready_path.exists():
            if process.poll() is not None:
                raise AssertionError(f"child exited before acquiring lock: {process.returncode}")
            if time.monotonic() >= deadline:
                raise AssertionError("child did not acquire lock")
            time.sleep(0.02)

        with pytest.raises(FileLockTimeoutError):
            with exclusive_file_lock(lock_path, timeout=0.2, poll_interval=0.02):
                raise AssertionError("second process lock body must not run")
    finally:
        release_path.write_text("release", encoding="utf-8")
        process.wait(timeout=5.0)

    assert process.returncode == 0
    with exclusive_file_lock(lock_path, timeout=1.0, poll_interval=0.01):
        assert lock_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing semantics only")
def test_atomic_write_waits_for_temporarily_open_destination_on_windows(tmp_path: Path) -> None:
    target = tmp_path / "open-target.json"
    target.write_bytes(b"old")
    handle = target.open("rb")

    def close_target() -> None:
        time.sleep(0.15)
        handle.close()

    closer = threading.Thread(target=close_target)
    closer.start()
    atomic_write_bytes(target, b"new")
    closer.join(timeout=2.0)

    assert target.read_bytes() == b"new"
