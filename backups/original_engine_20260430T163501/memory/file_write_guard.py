"""Lightweight per-file write lock guard."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
import json

_GLOBAL_LOCK = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _get_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _GLOBAL_LOCK:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def guarded_file_write(path: Path) -> Iterator[None]:
    lock = _get_lock(path)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, content.encode(encoding))


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with guarded_file_write(path):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)


def atomic_write_json(path: Path, payload: Any, ensure_ascii: bool = False, indent: int = 2) -> None:
    text = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent)
    atomic_write_text(path, text, encoding="utf-8")


def _file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return "text"
    if suffix in {".json", ".jsonl"}:
        return "json"
    if suffix in {".log"}:
        return "log"
    if suffix in {".db", ".sqlite"}:
        return "sqlite"
    return "binary"


def secure_read_bytes(path: Path, allow_plaintext: bool = False) -> bytes:
    if not path.exists():
        return b""
    data = path.read_bytes()
    from .config import get_config
    cfg = get_config()
    from .security import get_crypto_manager

    manager = get_crypto_manager(cfg)
    return manager.decrypt_after_read(data, target_path=path, allow_plaintext=allow_plaintext)


def secure_read_text(path: Path, encoding: str = "utf-8", allow_plaintext: bool = False) -> str:
    raw = secure_read_bytes(path, allow_plaintext=allow_plaintext)
    if not raw:
        return ""
    return raw.decode(encoding)


def secure_write_bytes(path: Path, content: bytes) -> None:
    from .config import get_config
    cfg = get_config()
    from .security import get_crypto_manager

    manager = get_crypto_manager(cfg)
    to_write = manager.encrypt_before_write(content, file_type=_file_type(path), target_path=path)
    atomic_write_bytes(path, to_write)


def secure_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    secure_write_bytes(path, content.encode(encoding))


def secure_append_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    current = secure_read_text(path, encoding=encoding, allow_plaintext=True) if path.exists() else ""
    secure_write_text(path, current + content, encoding=encoding)
