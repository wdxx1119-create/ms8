from __future__ import annotations

import json
import tempfile
from pathlib import Path

from memory.maintenance_policy import build_policy_actions, gather_policy_stats


def _mk_workspace() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="maintenance_shadow_"))
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    return ws


def test_shadow_sealed_triggers_replay_action() -> None:
    ws = _mk_workspace()
    shadow_dir = ws / "memory" / "security" / "shadow_data"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    (shadow_dir / "seal_manifest.json").write_text(
        json.dumps({"sealed": True, "mode": "sealed"}, ensure_ascii=False),
        encoding="utf-8",
    )
    stats = gather_policy_stats(ws, {"thresholds": {"shadow_spool_pending_threshold": 1}})
    actions = build_policy_actions(stats)
    names = [a.action for a in actions]
    assert "shadow_replay_spool" in names


def test_shadow_spool_pending_triggers_replay_action() -> None:
    ws = _mk_workspace()
    shadow_dir = ws / "memory" / "security" / "shadow_data"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    spool = shadow_dir / "shadow_spool.jsonl"
    rows = [
        {"spool_id": "a", "replayed": True},
        {"spool_id": "b", "replayed": False},
    ]
    spool.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    stats = gather_policy_stats(ws, {"thresholds": {"shadow_spool_pending_threshold": 1}})
    actions = build_policy_actions(stats)
    names = [a.action for a in actions]
    assert "shadow_replay_spool" in names


def test_shadow_replayed_spool_triggers_archive_action() -> None:
    ws = _mk_workspace()
    shadow_dir = ws / "memory" / "security" / "shadow_data"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    spool = shadow_dir / "shadow_spool.jsonl"
    rows = [{"spool_id": f"id{i}", "replayed": True} for i in range(45)]
    spool.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    stats = gather_policy_stats(ws, {"thresholds": {"shadow_spool_archive_threshold": 40}})
    actions = build_policy_actions(stats)
    names = [a.action for a in actions]
    assert "shadow_archive_spool" in names


def test_shadow_corrupt_lines_trigger_self_heal_action() -> None:
    ws = _mk_workspace()
    shadow_dir = ws / "memory" / "security" / "shadow_data"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    spool = shadow_dir / "shadow_spool.jsonl"
    spool.write_text('{"spool_id":"ok","replayed":false}\n{not-json}\n', encoding="utf-8")
    stats = gather_policy_stats(ws, {})
    actions = build_policy_actions(stats)
    names = [a.action for a in actions]
    assert "shadow_startup_self_heal" in names


def test_shadow_backup_interval_due_triggers_backup_sync_action() -> None:
    stats = {
        "shadow_sealed": False,
        "shadow_backup_hours_since_last": 48.0,
        "shadow_backup_interval_hours": 24.0,
    }
    actions = build_policy_actions(stats)
    names = [a.action for a in actions]
    assert "shadow_sync_verified_backup" in names


def test_self_check_fail_triggers_self_repair_action() -> None:
    stats = {
        "self_check_in_progress": False,
        "self_repair_enabled": True,
        "self_repair_on_warn": False,
        "self_check_fail_count": 1,
        "self_check_error_count": 0,
        "self_check_warn_count": 0,
    }
    actions = build_policy_actions(stats)
    names = [a.action for a in actions]
    assert "self_repair_auto" in names
