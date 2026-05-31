from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine_core import maintenance_manager as mod


class _Crypto:
    def __init__(self, enabled: bool = False, unlocked: bool = True):
        self._enabled = enabled
        self._unlocked = unlocked

    def is_enabled(self) -> bool:
        return self._enabled

    def is_unlocked(self) -> bool:
        return self._unlocked

    def encrypt_before_write(self, raw: bytes, **_: object) -> bytes:
        return raw


class _Store:
    def __init__(self, memory_md: Path):
        self._path = memory_md
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("# MEMORY\n", encoding="utf-8")

    def read_memory_md(self) -> str:
        return self._path.read_text(encoding="utf-8")

    def write_memory_md(self, text: str) -> None:
        self._path.write_text(text, encoding="utf-8")


def _cfg(tmp_path: Path) -> dict:
    ws = tmp_path / "ws"
    mem = ws / "memory"
    return {
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
                    "backup_enabled": True,
                    "backup_keep": 2,
                    "cleanup_enabled": True,
                    "sync_memory_md": True,
                    "restore_drill_enabled": True,
                    "restore_drill_keep_reports": 2,
                    "cleanup_legacy_root_backups": True,
                    "legacy_backup_keep": 1,
                    "cleanup_snapshots_keep": 1,
                }
            }
        },
    }


def _mk_manager(tmp_path: Path, monkeypatch, *, crypto_enabled=False, unlocked=True):
    cfg = _cfg(tmp_path)
    store = _Store(cfg["memory_md"])
    monkeypatch.setattr(mod, "get_crypto_manager", lambda _c: _Crypto(enabled=crypto_enabled, unlocked=unlocked))
    return mod.MaintenanceManager(cfg, store), cfg, store


def test_parse_due_and_default_state(tmp_path: Path, monkeypatch) -> None:
    mgr, cfg, _ = _mk_manager(tmp_path, monkeypatch)
    assert mgr.state_file.exists()
    st = json.loads(mgr.state_file.read_text(encoding="utf-8"))
    assert "last_backup_at" in st
    assert mgr._parse_time("bad") is None
    assert mgr._due("", 24) is True
    past = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    assert mgr._due(past, 24) is True
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert mgr._due(recent, 24) is False
    assert cfg["workspace_dir"].exists()


def test_blocked_by_lock_and_disabled_paths(tmp_path: Path, monkeypatch) -> None:
    mgr, _, _ = _mk_manager(tmp_path, monkeypatch, crypto_enabled=True, unlocked=False)
    blocked = mgr._blocked_by_lock("run")
    assert blocked and blocked["status"] == "blocked"
    assert mgr.backup_assets()["status"] == "blocked"
    assert mgr.run_maintenance()["status"] == "blocked"


def test_ensure_memory_md_and_sync_skip_paths(tmp_path: Path, monkeypatch) -> None:
    mgr, cfg, _ = _mk_manager(tmp_path, monkeypatch)
    assert mgr.ensure_memory_md() is False

    auto = cfg["memory_dir"] / "auto_memory_records.jsonl"
    auto.parent.mkdir(parents=True, exist_ok=True)
    auto.write_text("", encoding="utf-8")
    out = mgr.sync_memory_md_from_auto_memory()
    assert out["status"] == "skipped"

    # no file branch
    auto.unlink()
    out2 = mgr.sync_memory_md_from_auto_memory()
    assert out2["reason"] == "no_auto_memory_records"


def test_sync_memory_md_success_and_already_synced(tmp_path: Path, monkeypatch) -> None:
    mgr, cfg, store = _mk_manager(tmp_path, monkeypatch)
    auto = cfg["memory_dir"] / "auto_memory_records.jsonl"
    auto.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": "r1",
        "status": "accepted",
        "confidence": 0.9,
        "category": "decision",
        "normalized_text": "Use option B",
        "meta": {},
    }
    auto.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    out = mgr.sync_memory_md_from_auto_memory()
    assert out["status"] == "success"
    md = store.read_memory_md()
    assert "Auto Memory Sync" in md
    assert "Use option B" in md

    # second call same day should skip
    out2 = mgr.sync_memory_md_from_auto_memory()
    assert out2["status"] == "skipped"


def test_cleanup_memory_md_quality_and_legacy_cleanup(tmp_path: Path, monkeypatch) -> None:
    mgr, cfg, store = _mk_manager(tmp_path, monkeypatch)
    store.write_memory_md(
        "## verification section\n- smoke test line\n- keep me\n- keep me\n",
    )
    cleaned = mgr.cleanup_memory_md_quality()
    assert cleaned["status"] == "success"
    out = store.read_memory_md()
    assert "verification section" not in out.lower()
    assert out.count("keep me") == 1

    # legacy dirs cleanup
    (cfg["workspace_dir"] / "backup_a").mkdir(parents=True, exist_ok=True)
    (cfg["workspace_dir"] / "backup_b").mkdir(parents=True, exist_ok=True)
    (cfg["memory_dir"] / "backup_before_fix_x").mkdir(parents=True, exist_ok=True)
    snap = cfg["memory_dir"] / "cleanup_snapshots"
    (snap / "a").mkdir(parents=True, exist_ok=True)
    (snap / "b").mkdir(parents=True, exist_ok=True)
    legacy = mgr.cleanup_legacy_backup_dirs()
    assert legacy["status"] == "success"


def test_backup_assets_restore_drill_and_run_maintenance(tmp_path: Path, monkeypatch) -> None:
    mgr, cfg, store = _mk_manager(tmp_path, monkeypatch)
    # create backup source files
    (cfg["workspace_dir"] / "MEMORY.md").write_text("# M", encoding="utf-8")
    (cfg["workspace_dir"] / "memory").mkdir(parents=True, exist_ok=True)
    for p in [
        cfg["workspace_dir"] / "memory" / "memory.db",
        cfg["workspace_dir"] / "memory" / "knowledge_graph.db",
        cfg["workspace_dir"] / "memory" / "auto_memory_records.jsonl",
        cfg["workspace_dir"] / "memory" / "auto_memory_index.json",
    ]:
        p.write_text("x", encoding="utf-8")

    b = mgr.backup_assets()
    assert b["status"] == "success"
    assert b["copied"]
    rd = mgr.run_restore_drill()
    assert rd["status"] in {"success", "failed"}
    assert Path(rd["restore_dir"]).exists()

    # isolate run_maintenance by monkeypatching heavy steps
    monkeypatch.setattr(mgr, "backup_assets", lambda: {"status": "success"})
    monkeypatch.setattr(mgr, "sync_memory_md_from_auto_memory", lambda: {"status": "success"})
    monkeypatch.setattr(mgr, "cleanup_old_low_importance_logs", lambda: {"status": "success"})
    monkeypatch.setattr(mgr, "cleanup_memory_md_quality", lambda: {"status": "success"})
    monkeypatch.setattr(mgr, "cleanup_legacy_backup_dirs", lambda: {"status": "success"})
    monkeypatch.setattr(mgr, "run_restore_drill", lambda: {"status": "success"})
    run = mgr.run_maintenance(force=True)
    assert run["status"] == "success"
    assert run["ran"] is True

