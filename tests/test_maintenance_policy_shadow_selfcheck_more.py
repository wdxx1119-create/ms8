from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine_core import maintenance_policy as mp


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def test_gather_policy_stats_shadow_and_selfcheck_branches(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path
    memory_dir = workspace / "memory"
    now = datetime.now(timezone.utc)

    (workspace / "MEMORY.md").write_text("memo\n", encoding="utf-8")
    _write(memory_dir / "memory_blocks.json", {"blocks": []})
    _write_jsonl(memory_dir / "auto_memory_records.jsonl", [{"id": "r1", "content": "x"}])
    # include one old and one fresh daily log for tiering count
    (memory_dir / f"{(now - timedelta(days=10)).date().isoformat()}-old.md").write_text("old", encoding="utf-8")
    (memory_dir / f"{(now - timedelta(days=1)).date().isoformat()}-new.md").write_text("new", encoding="utf-8")

    # shadow spool includes replayed/pending/corrupt lines
    spool = memory_dir / "security" / "shadow_data" / "shadow_spool.jsonl"
    spool.parent.mkdir(parents=True, exist_ok=True)
    spool.write_text(
        "\n".join(
            [
                json.dumps({"replayed": False}),
                json.dumps({"replayed": True}),
                "{bad json}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # seal manifest with recent history
    _write(
        memory_dir / "security" / "shadow_data" / "seal_manifest.json",
        {
            "sealed": True,
            "sealed_at": (now - timedelta(hours=2)).isoformat(),
            "history": [
                {"event": "seal", "ts": (now - timedelta(hours=1)).isoformat()},
                {"event": "seal", "ts": (now - timedelta(hours=2)).isoformat()},
                {"event": "other", "ts": (now - timedelta(hours=3)).isoformat()},
            ],
        },
    )
    _write_jsonl(
        memory_dir / "security" / "shadow_data" / "shadow_verify.jsonl",
        [{"ok": True}, {"ok": False}],
    )

    # backup manifest under HOME fallback path
    fake_home = tmp_path / "fake_home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    _write(
        fake_home / ".shadow_backup" / "backup_manifest.json",
        [{"ts": (now - timedelta(hours=6)).isoformat()}],
    )

    _write(memory_dir / "write_fail_state.json", {"consecutive_failures": 4, "recent_failures_30s": 2})

    # self-check latest/history + in-progress marker
    reports = memory_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "check_in_progress.json").write_text("{}", encoding="utf-8")
    _write(
        reports / "self_check_latest.json",
        {
            "requested_level": "L4",
            "finished_at": (now - timedelta(minutes=5)).isoformat(),
            "summary": {"warn": 2, "fail": 1, "error": 0, "exit_code": 1},
        },
    )
    _write(reports / "self_check_history" / "l1.json", {"requested_level": "L1", "finished_at": now.isoformat()})
    _write(
        reports / "self_check_history" / "full.json",
        {"requested_level": "FULL", "finished_at": (now - timedelta(hours=3)).isoformat()},
    )
    _write(
        reports / "self_check_history" / "l4.json",
        {"requested_level": "L4", "finished_at": (now - timedelta(hours=8)).isoformat()},
    )

    _write_jsonl(
        memory_dir / "alerts.jsonl",
        [
            {"timestamp": now.isoformat(), "severity": "critical"},
            {"timestamp": now.isoformat(), "severity": "error"},
            {"timestamp": now.isoformat(), "severity": "warning"},
        ],
    )
    _write_jsonl(
        memory_dir / "maintenance_policy_log.jsonl",
        [
            {"action": "shadow_auto_seal", "timestamp": now.isoformat()},
            {"action": "shadow_recovery_drill", "timestamp": (now - timedelta(hours=30)).isoformat()},
            {"action": "trigger_batch_extract_kg", "timestamp": (now - timedelta(days=1)).isoformat()},
        ],
    )

    stats = mp.gather_policy_stats(
        workspace,
        {"thresholds": {"tiering_retention_days": 7, "alerts_window_minutes": 120}},
    )
    assert stats["shadow_spool_pending"] == 1
    assert stats["shadow_spool_replayed"] == 1
    assert stats["shadow_corrupt_line_count"] == 1
    assert stats["shadow_sealed"] is True
    assert stats["shadow_seal_events_24h"] >= 2
    assert stats["shadow_checkpoint_mismatch"] is True
    assert 0.0 <= stats["shadow_backup_hours_since_last"] <= 24.0
    assert stats["write_fail_consecutive"] == 4
    assert stats["self_check_in_progress"] is True
    assert stats["self_check_fail_count"] == 1
    assert stats["self_check_warn_count"] == 2
    assert stats["self_check_latest_exit_code"] == 1
    assert stats["alerts_recent_critical"] == 1
    assert stats["alerts_recent_error"] == 1
    assert stats["alerts_recent_warning"] == 1
    assert stats["tiering_candidate_count"] >= 1


def test_self_check_repair_predicates_negative_paths() -> None:
    base = {
        "self_check_enabled": True,
        "self_check_in_progress": False,
        "self_check_l1_latest_age_minutes": 1.0,
        "self_check_l1_interval_minutes": 30.0,
        "self_check_l2l3_latest_age_minutes": 30.0,
        "self_check_l2l3_interval_hours": 24.0,
        "self_check_l4_latest_age_minutes": 60.0,
        "self_check_l4_interval_hours": 168.0,
        "self_repair_enabled": True,
        "self_check_fail_count": 0,
        "self_check_error_count": 0,
        "self_check_warn_count": 0,
        "alerts_recent_critical": 0,
        "alerts_recent_error": 0,
        "self_repair_on_warn": False,
    }
    assert mp.should_run_self_check_l1(base) is False
    assert mp.should_run_self_check_l2l3(base) is False
    assert mp.should_run_self_check_l4(base) is False
    assert mp.should_run_self_repair(base) is False

    with_warn = dict(base)
    with_warn["self_repair_on_warn"] = True
    with_warn["self_check_warn_count"] = 2
    assert mp.should_run_self_repair(with_warn) is True
