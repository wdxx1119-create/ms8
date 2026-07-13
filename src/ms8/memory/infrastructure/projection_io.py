"""Atomic JSON helpers for disposable projection artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..domain.ledger import canonical_json
from .durable_io import atomic_write_bytes


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> str:
    """Write canonical JSON atomically and return the final artifact digest."""

    data = (canonical_json(payload) + "\n").encode("utf-8")
    atomic_write_bytes(path, data)
    return sha256_bytes(data)


def read_json_object(path: Path) -> dict[str, Any] | None:
    """Read a JSON object, returning ``None`` for missing or malformed data."""

    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
