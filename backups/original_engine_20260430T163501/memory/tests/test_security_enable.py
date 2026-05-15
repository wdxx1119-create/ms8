from __future__ import annotations

from pathlib import Path

from memory.config import get_config
from memory.file_store import FileMemoryStore
from memory.maintenance_manager import MaintenanceManager
from memory.security import get_crypto_manager
from memory.security.file_crypto import MAGIC


def test_enable_encryption_and_backup_encrypted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_WORKSPACE", str(tmp_path))
    cfg = get_config()
    fs = FileMemoryStore()
    fs.write_memory_md("secret line")
    records = Path(cfg["memory_dir"]) / "auto_memory_records.jsonl"
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text('{"id":"x","text":"hello"}\n', encoding="utf-8")

    manager = get_crypto_manager(cfg)
    out = manager.enable_encryption("Passw0rd-Strong")
    assert out["status"] == "success"
    assert out["recovery_key"]
    assert manager.status()["session_state"] == "unlocked"

    raw_md = Path(cfg["memory_md"]).read_bytes()
    assert raw_md.startswith(MAGIC)

    mm = MaintenanceManager(cfg, fs)
    backup = mm.backup_assets()
    assert backup["status"] == "success"
    copied = [Path(p) for p in backup["copied"]]
    md_backup = next((p for p in copied if p.name.lower().endswith(".md")), None)
    assert md_backup is not None
    assert md_backup.read_bytes().startswith(MAGIC)

