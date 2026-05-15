from __future__ import annotations

from pathlib import Path

import pytest

from app.memory.repository import MemoryRepository
from app.schemas.pipeline_schema import MemoryRecord
from memory.config import get_config
from memory.security import CryptoLockedError, get_crypto_manager
from memory.security.file_crypto import MAGIC


def test_repository_encrypted_storage_and_readback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_WORKSPACE", str(tmp_path))
    cfg = get_config()
    manager = get_crypto_manager(cfg)
    manager.enable_encryption("Passw0rd-Strong")

    repo_path = Path(cfg["memory_dir"]) / "auto_memory_records.jsonl"
    repo = MemoryRepository(repo_path)
    rec = MemoryRecord(
        text="sensitive memory",
        normalized_text="sensitive memory",
        category="decision",
        confidence=0.9,
        source="unit_test",
        meta={"id": "unit-1"},
    )
    saved = repo.save(rec)
    assert saved["id"] == "unit-1"

    raw = repo_path.read_bytes()
    assert raw.startswith(MAGIC)
    assert b"sensitive memory" not in raw

    manager.lock()
    with pytest.raises(CryptoLockedError):
        _ = repo.list_recent(limit=5)

    assert manager.unlock("Passw0rd-Strong") is True
    rows = repo.list_recent(limit=5)
    assert rows
    assert rows[0]["normalized_text"] == "sensitive memory"

