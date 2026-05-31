from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine_core import maintenance_policy as mp


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def test_gather_policy_stats_reads_workspace_and_threshold_overrides(tmp_path: Path) -> None:
    workspace = tmp_path
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    (workspace / "MEMORY.md").write_text("\n".join(["a", "b", "c"]), encoding="utf-8")
    _write_jsonl(
        memory_dir / "auto_memory_records.jsonl",
        [
            {"id": "r1", "text": "normal"},
            {"text": "verify_canary sample", "meta": {"_auto_check_test": True}},
        ],
    )
    _write_json(memory_dir / "semantic_cache.json", {"k1": {"dense": None}, "k2": {"dense": [0.1]}})
    _write_jsonl(memory_dir / "auto_memory_review_queue.jsonl", [{"decision": "pending"}, {"decision": "accepted"}])
    _write_jsonl(
        memory_dir / "knowledge_feedback.jsonl",
        [{"tier": "t1", "trust": "high"}, {"tier": "t1", "trust": "high"}, {"tier": "t2", "trust": "low"}],
    )
    now = datetime.now(timezone.utc)
    _write_json(
        memory_dir / "threshold_suggestions_pending.json",
        {
            "items": [
                {"status": "pending", "created_at": (now - timedelta(minutes=10)).isoformat()},
                {"status": "accepted", "created_at": now.isoformat()},
            ]
        },
    )
    _write_jsonl(
        memory_dir / "maintenance_policy_log.jsonl",
        [
            {"action": "trigger_batch_extract_kg", "timestamp": (now - timedelta(minutes=30)).isoformat()},
            {"action": "trigger_memory_tiering", "timestamp": (now - timedelta(minutes=20)).isoformat()},
            {"action": "shadow_auto_seal", "timestamp": now.isoformat()},
        ],
    )
    _write_json(
        memory_dir / "reports" / "self_check_latest.json",
        {
            "requested_level": "L4",
            "finished_at": (now - timedelta(minutes=5)).isoformat(),
            "summary": {"warn": 1, "fail": 0, "error": 0, "exit_code": 0},
        },
    )
    _write_json(
        memory_dir / "reports" / "self_check_history" / "l1.json",
        {"requested_level": "L1", "finished_at": (now - timedelta(minutes=30)).isoformat()},
    )
    _write_json(
        memory_dir / "reports" / "self_check_history" / "full.json",
        {"requested_level": "FULL", "finished_at": (now - timedelta(hours=2)).isoformat()},
    )
    _write_json(
        memory_dir / "reports" / "self_check_history" / "l4.json",
        {"requested_level": "L4", "finished_at": (now - timedelta(hours=10)).isoformat()},
    )
    _write_jsonl(
        memory_dir / "alerts.jsonl",
        [
            {"timestamp": now.isoformat(), "severity": "critical"},
            {"timestamp": now.isoformat(), "severity": "error"},
            {"timestamp": now.isoformat(), "severity": "warning"},
        ],
    )
    _write_json(memory_dir / "write_fail_state.json", {"consecutive_failures": 2, "recent_failures_30s": 1})
    _write_json(memory_dir / "memory_blocks.json", {"blocks": []})
    (memory_dir / f"{(now - timedelta(days=20)).date().isoformat()}-daily-log.md").write_text("old", encoding="utf-8")

    cfg = {
        "threshold_auto_navigate_enabled": True,
        "auto_seal_daily_limit": 7,
        "thresholds": {
            "memory_md_lines_threshold": 123,
            "tiering_retention_days": 1,
            "alerts_window_minutes": 60,
        },
        "self_check": {
            "enabled": True,
            "l1_interval_minutes": 15,
            "l2l3_interval_hours": 12,
            "l4_interval_hours": 96,
            "self_repair_enabled": False,
            "self_repair_on_warn": True,
        },
    }
    stats = mp.gather_policy_stats(workspace, cfg)

    assert stats["memory_md_lines"] == 3
    assert stats["memory_md_lines_threshold"] == 123
    assert stats["test_pollution_ratio"] > 0.0
    assert stats["missing_record_ids"] == 1
    assert stats["auto_check_test_residual_count"] == 1
    assert stats["semantic_dense_missing"] == 1
    assert stats["review_backlog_pending"] == 1
    assert stats["feedback_recent_count"] == 3
    assert stats["threshold_auto_navigate_enabled"] is True
    assert stats["threshold_suggestion_pending_count"] == 1
    assert stats["write_fail_consecutive"] == 2
    assert stats["write_fail_recent_30s"] == 1
    assert stats["auto_seal_daily_limit"] == 7
    assert stats["auto_seal_triggered_today"] >= 1
    assert stats["self_check_l1_interval_minutes"] == 15.0
    assert stats["self_check_l2l3_interval_hours"] == 12.0
    assert stats["self_check_l4_interval_hours"] == 96.0
    assert stats["self_repair_enabled"] is False
    assert stats["self_repair_on_warn"] is True
    assert stats["alerts_recent_critical"] == 1
    assert stats["alerts_recent_error"] == 1
    assert stats["alerts_recent_warning"] == 1
    assert isinstance(stats["kg_batch_extract_pending_signal"], bool)
    assert stats["tiering_candidate_count"] >= 1


def test_gather_policy_stats_tolerates_invalid_json_and_uses_defaults(tmp_path: Path) -> None:
    workspace = tmp_path
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (workspace / "MEMORY.md").write_text("x\n", encoding="utf-8")
    (memory_dir / "semantic_cache.json").write_text("{bad json", encoding="utf-8")
    (memory_dir / "threshold_suggestions_pending.json").write_text("{oops", encoding="utf-8")
    _write_json(memory_dir / "reports" / "self_check_latest.json", {"bad": "shape"})
    (memory_dir / "alerts.jsonl").write_text('{"timestamp":"bad","severity":"critical"}\n', encoding="utf-8")

    stats = mp.gather_policy_stats(workspace, None)
    assert stats["memory_md_lines"] == 1
    assert stats["semantic_dense_missing"] == 0
    assert stats["threshold_suggestion_pending_count"] == 0
    assert stats["self_check_latest_exit_code"] in {0, 1, 2}
    assert stats["alerts_recent_critical"] == 0


def test_build_policy_actions_dedup_and_priority_order() -> None:
    stats = {
        "alerts_recent_critical": 1,
        "self_check_l1_latest_age_minutes": 999,
        "self_check_l2l3_latest_age_minutes": 99999,
        "self_check_l4_latest_age_minutes": 999999,
        "self_repair_enabled": True,
        "self_check_fail_count": 1,
        "review_backlog_pending": 999,
        "review_backlog_pending_threshold": 80,
        "review_backlog_pending_soft_threshold": 50,
        "review_backlog_stale_hours": 100.0,
        "review_backlog_stale_hours_threshold": 24.0,
    }
    actions = mp.build_policy_actions(stats)
    names = [a.action for a in actions]

    assert len(names) == len(set(names))
    assert [a.priority for a in actions] == sorted(a.priority for a in actions)
    assert "self_check_l2l3" in names
    assert "self_check_l4" in names
    assert "self_repair_auto" in names
    assert "trigger_batch_review" in names
