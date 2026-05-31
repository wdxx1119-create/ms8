from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine_core.monitoring import MemoryMonitoring


def _mk_monitor(tmp_path: Path) -> MemoryMonitoring:
    cfg = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {
            "memory": {
                "monitoring": {
                    "alerts": {
                        "enabled": True,
                        "alert_cooldown_hours": 2,
                        "no_new_memory_hours": 1,
                        "alert_log_file": str(tmp_path / "memory" / "alerts.jsonl"),
                    }
                }
            }
        },
    }
    return MemoryMonitoring(cfg)


def test_alert_with_cooldown_emits_once_within_window(tmp_path: Path) -> None:
    mon = _mk_monitor(tmp_path)
    first = mon._alert_with_cooldown("c1", "msg", "warning", cooldown_hours=2)
    second = mon._alert_with_cooldown("c1", "msg", "warning", cooldown_hours=2)
    assert first is not None
    assert second is None


def test_detect_anomalies_reports_stale_and_slo_breach(tmp_path: Path) -> None:
    mon = _mk_monitor(tmp_path)
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    auto_entries = [{"timestamp": old_ts}]
    snapshot = {
        "rates": {"capture_rate": 0.1, "injection_rate": 0.1},
        "slo": {
            "checks": {"capture_rate": False, "injection_rate": False},
            "targets": {"capture_rate_min": 0.85, "injection_rate_min": 0.8},
        },
    }
    emitted = mon._detect_anomalies(snapshot, auto_entries)
    codes = {x["code"] for x in emitted}
    assert "no_new_memory" in codes
    assert "capture_rate_breach" in codes
    assert "injection_rate_breach" in codes


def test_threshold_suggestion_stats_counts(tmp_path: Path) -> None:
    mon = _mk_monitor(tmp_path)
    pending_file = tmp_path / "memory" / "threshold_suggestions_pending.json"
    approval_log = tmp_path / "memory" / "threshold_suggestions_approval_log.jsonl"
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending_file.write_text(
        json.dumps(
            {
                "items": [
                    {"id": "a", "status": "pending"},
                    {"id": "b", "status": "approved"},
                    {"id": "c", "status": "rejected"},
                ],
                "last_generated_at": "2026-01-01T00:00:00+00:00",
                "last_applied_at": "2026-01-02T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    approval_log.write_text(json.dumps({"event": "approved"}, ensure_ascii=False) + "\n", encoding="utf-8")

    stats = mon._threshold_suggestion_stats()
    assert stats["total_items"] == 3
    assert stats["pending_count"] == 1
    assert stats["approved_count"] == 1
    assert stats["rejected_count"] == 1
    assert stats["recent_approval_events"] == 1

