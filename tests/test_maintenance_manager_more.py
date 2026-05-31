from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core import maintenance_manager as mod


class _Crypto:
    def __init__(self, enabled: bool = False, unlocked: bool = True) -> None:
        self._enabled = enabled
        self._unlocked = unlocked

    def is_enabled(self) -> bool:
        return self._enabled

    def is_unlocked(self) -> bool:
        return self._unlocked

    def encrypt_before_write(self, raw: bytes, **_: object) -> bytes:
        return raw


class _Store:
    def __init__(self, p: Path) -> None:
        self._p = p
        self._p.parent.mkdir(parents=True, exist_ok=True)
        self._p.write_text("# MEMORY\n", encoding="utf-8")

    def read_memory_md(self) -> str:
        return self._p.read_text(encoding="utf-8")

    def write_memory_md(self, text: str) -> None:
        self._p.write_text(text, encoding="utf-8")


def _mk(tmp_path: Path, monkeypatch):
    ws = tmp_path / "ws"
    mem = ws / "memory"
    cfg = {
        "workspace_dir": ws,
        "memory_dir": mem,
        "memory_md": ws / "MEMORY.md",
        "settings": {
            "memory": {
                "security": {"require_unlock_for_maintenance": True},
                "maintenance": {
                    "enabled": True,
                    "state_file": "memory/maintenance_state.json",
                    "backup_dir": "memory/backups",
                    "sync_audit_file": "memory/memory_sync_audit.jsonl",
                    "sync_memory_md": True,
                    "cleanup_enabled": True,
                    "restore_drill_enabled": True,
                },
            }
        },
    }
    monkeypatch.setattr(mod, "get_crypto_manager", lambda _c: _Crypto(False, True))
    mgr = mod.MaintenanceManager(cfg, _Store(cfg["memory_md"]))
    return mgr, cfg


def test_apply_tiering_plan_moves_and_skips(tmp_path: Path, monkeypatch):
    mgr, _cfg = _mk(tmp_path, monkeypatch)
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    dst = tmp_path / "dir" / "a.txt"
    existing = tmp_path / "dir" / "b.txt"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("y", encoding="utf-8")
    result = mgr.apply_tiering_plan(
        [
            {"source_path": str(src), "target_path": str(dst)},
            {"source_path": str(tmp_path / "missing.txt"), "target_path": str(tmp_path / "moved.txt")},
            {"source_path": str(existing), "target_path": str(existing)},
        ]
    )
    assert result["status"] == "success"
    assert str(dst) in result["moved"]
    assert any(s.startswith("missing:") for s in result["skipped"])
    assert any(s.startswith("exists:") for s in result["skipped"])


def test_cleanup_old_low_importance_logs_disabled(tmp_path: Path, monkeypatch):
    mgr, _cfg = _mk(tmp_path, monkeypatch)
    mgr.settings["cleanup_enabled"] = False
    out = mgr.cleanup_old_low_importance_logs()
    assert out["status"] == "disabled"


def test_cleanup_old_low_importance_logs_archives_old_low_value(tmp_path: Path, monkeypatch):
    mgr, cfg = _mk(tmp_path, monkeypatch)
    daily = cfg["memory_dir"] / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    old = daily / "2020-01-01-log.md"
    old.write_text("just notes", encoding="utf-8")
    important = daily / "2020-01-02-log.md"
    important.write_text("这是必须完成的决定", encoding="utf-8")
    monkeypatch.setattr(mod, "list_daily_log_files", lambda *_: [old, important])
    mgr.settings["cleanup_days"] = 30
    out = mgr.cleanup_old_low_importance_logs()
    assert out["status"] == "success"
    assert any("low_priority" in p for p in out["moved"])
    assert important.exists()


def test_cleanup_legacy_backup_dirs_disabled(tmp_path: Path, monkeypatch):
    mgr, _cfg = _mk(tmp_path, monkeypatch)
    mgr.settings["cleanup_legacy_root_backups"] = False
    out = mgr.cleanup_legacy_backup_dirs()
    assert out["status"] == "disabled"


def test_run_restore_drill_disabled_and_no_backup(tmp_path: Path, monkeypatch):
    mgr, _cfg = _mk(tmp_path, monkeypatch)
    mgr.settings["restore_drill_enabled"] = False
    out = mgr.run_restore_drill()
    assert out["status"] == "disabled"
    mgr.settings["restore_drill_enabled"] = True
    out2 = mgr.run_restore_drill()
    assert out2["status"] == "skipped"
    assert out2["reason"] == "no_backup_found"


def test_sync_memory_md_disabled_and_already_synced_today(tmp_path: Path, monkeypatch):
    mgr, cfg = _mk(tmp_path, monkeypatch)
    mgr.settings["sync_memory_md"] = False
    out = mgr.sync_memory_md_from_auto_memory()
    assert out["status"] == "disabled"
    mgr.settings["sync_memory_md"] = True

    auto = cfg["memory_dir"] / "auto_memory_records.jsonl"
    auto.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": "x1",
        "status": "accepted",
        "confidence": 0.95,
        "category": "decision",
        "normalized_text": "choose option c",
        "meta": {},
    }
    auto.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    first = mgr.sync_memory_md_from_auto_memory()
    second = mgr.sync_memory_md_from_auto_memory()
    assert first["status"] == "success"
    assert second["status"] == "skipped"
    assert second["reason"] in {"already_synced_today", "no_new_high_quality_records"}


def test_run_maintenance_disabled_and_not_due(tmp_path: Path, monkeypatch):
    mgr, _cfg = _mk(tmp_path, monkeypatch)
    mgr.enabled = False
    out = mgr.run_maintenance(force=False)
    assert out["status"] == "disabled"
