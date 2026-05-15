from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ms8.engine_core.maintenance_policy import build_policy_actions, gather_policy_stats


def _mk_workspace() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="maintenance_kg_tiering_"))
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    return ws


def test_kg_batch_extract_action_triggered_by_source_change() -> None:
    ws = _mk_workspace()
    memory_md = ws / "MEMORY.md"
    memory_md.write_text("# memory\nnew content\n", encoding="utf-8")

    stats = gather_policy_stats(
        ws,
        {
            "thresholds": {
                "kg_batch_extract_source_lag_minutes_threshold": 0,
            }
        },
    )
    actions = build_policy_actions(stats)
    names = [a.action for a in actions]
    assert "trigger_batch_extract_kg" in names


def test_tiering_action_triggered_by_old_daily_logs() -> None:
    ws = _mk_workspace()
    old_log = ws / "memory" / "2026-01-01-learning.md"
    old_log.write_text("old log", encoding="utf-8")
    (ws / "MEMORY.md").write_text("# memory\n", encoding="utf-8")

    stats = gather_policy_stats(
        ws,
        {
            "thresholds": {
                "tiering_retention_days": 1,
                "tiering_candidate_threshold": 1,
            }
        },
    )
    actions = build_policy_actions(stats)
    names = [a.action for a in actions]
    assert "trigger_memory_tiering" in names


def test_recent_tiering_not_triggered_when_no_candidates() -> None:
    ws = _mk_workspace()
    recent_log = ws / "memory" / "2099-01-01-learning.md"
    recent_log.write_text("future/recent log", encoding="utf-8")
    (ws / "MEMORY.md").write_text("# memory\n", encoding="utf-8")

    stats = gather_policy_stats(
        ws,
        {
            "thresholds": {
                "tiering_retention_days": 36500,
                "tiering_candidate_threshold": 1,
            }
        },
    )
    actions = build_policy_actions(stats)
    names = [a.action for a in actions]
    assert "trigger_memory_tiering" not in names


def test_kg_pending_signal_clears_after_logged_run() -> None:
    ws = _mk_workspace()
    mem = ws / "memory"
    (ws / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    policy_log = mem / "maintenance_policy_log.jsonl"
    rows = [
        {
            "timestamp": "2999-01-01T00:00:00+00:00",
            "action": "trigger_batch_extract_kg",
            "result": {"status": "success"},
        }
    ]
    policy_log.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    stats = gather_policy_stats(ws, {"thresholds": {"kg_batch_extract_source_lag_minutes_threshold": 5}})
    assert bool(stats.get("kg_batch_extract_pending_signal", True)) is False


def test_threshold_auto_navigate_action_triggered_when_pending_exists() -> None:
    ws = _mk_workspace()
    mem = ws / "memory"
    (ws / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    pending = {
        "items": [
            {
                "approval_id": "thr-1",
                "status": "pending",
                "created_at": "2026-01-01T00:00:00+00:00",
                "stats": {"recent_count": 200},
                "suggestions": [],
            }
        ]
    }
    (mem / "threshold_suggestions_pending.json").write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
    stats = gather_policy_stats(
        ws,
        {
            "threshold_auto_navigate_enabled": True,
            "thresholds": {"threshold_suggestion_pending_min_age_minutes": 0},
        },
    )
    actions = build_policy_actions(stats)
    names = [a.action for a in actions]
    assert "auto_navigate_threshold_suggestions" in names
