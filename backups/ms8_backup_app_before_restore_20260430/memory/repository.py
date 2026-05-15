from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ms8.engine_core.config import get_config
from ms8.engine_core.security import CryptoLockedError, get_crypto_manager
from ms8.engine_core.security.file_crypto import is_encrypted_blob


class MemoryRepository:
    def __init__(self, path: Any | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._rows: list[dict[str, Any]] = []
        self._crypto = None
        if self.path is not None:
            try:
                self._crypto = get_crypto_manager(get_config())
            except Exception:
                self._crypto = None

    def add(self, record: Any) -> dict[str, Any]:
        row = asdict(record) if hasattr(record, "__dataclass_fields__") else dict(record)
        row.setdefault("id", str((row.get("meta") or {}).get("id") or ""))
        self._rows.append(row)
        self._persist()
        return row

    def save(self, record: Any) -> dict[str, Any]:
        return self.add(record)

    def list(self) -> list[dict[str, Any]]:
        self._load_from_disk()
        return list(self._rows)

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        self._load_from_disk()
        return list(self._rows)[-max(1, int(limit)) :]

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(r, ensure_ascii=False) for r in self._rows) + ("\n" if self._rows else "")
        raw = content.encode("utf-8")
        if self._crypto and self._crypto.is_enabled():
            raw = self._crypto.encrypt_before_write(raw, file_type="json", target_path=self.path)
        self.path.write_bytes(raw)

    def _load_from_disk(self) -> None:
        if self.path is None or not self.path.exists():
            return
        raw = self.path.read_bytes()
        if not raw:
            self._rows = []
            return
        if is_encrypted_blob(raw):
            if not self._crypto or not self._crypto.is_enabled() or not self._crypto.is_unlocked():
                raise CryptoLockedError("memory_security_locked")
            raw = self._crypto.decrypt_after_read(raw, target_path=self.path, allow_plaintext=True)
        text = raw.decode("utf-8", errors="ignore")
        rows: list[dict[str, Any]] = []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        self._rows = rows
