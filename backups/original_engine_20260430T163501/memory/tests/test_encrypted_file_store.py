from __future__ import annotations

from pathlib import Path

import pytest

from memory.config import get_config
from memory.file_store import FileMemoryStore
from memory.security import CryptoLockedError, get_crypto_manager
from memory.security.file_crypto import MAGIC


def test_memory_md_encrypted_and_locked_reads_fail(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_WORKSPACE", str(tmp_path))
    cfg = get_config()
    store = FileMemoryStore()
    manager = get_crypto_manager(cfg)
    manager.enable_encryption("Passw0rd-Strong")

    content = "my critical memory payload"
    store.write_memory_md(content)
    raw = Path(cfg["memory_md"]).read_bytes()
    assert raw.startswith(MAGIC)
    assert content.encode("utf-8") not in raw

    manager.lock()
    with pytest.raises(CryptoLockedError):
        _ = store.read_memory_md()

    assert manager.unlock("Passw0rd-Strong") is True
    assert content in store.read_memory_md()

