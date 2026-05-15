from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ms8.engine_core.monitoring import MemoryMonitoring


def test_shadow_replay_summary_in_monitoring() -> None:
    ws = Path(tempfile.mkdtemp(prefix="monitor_shadow_"))
    mem = ws / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (ws / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    (mem / "learning_task_log.jsonl").write_text("", encoding="utf-8")
    (mem / "memory_usage_log.jsonl").write_text("", encoding="utf-8")
    (mem / "auto_memory_log.json").write_text(json.dumps({"entries": []}), encoding="utf-8")

    rows = [
        {
            "timestamp": "2026-04-21T00:00:00",
            "action": "shadow_replay_spool",
            "result": {
                "status": "partial",
                "replayed": 2,
                "skipped": 1,
                "failed": 1,
                "remaining": 1,
            },
        },
        {
            "timestamp": "2026-04-21T01:00:00",
            "action": "shadow_replay_spool",
            "result": {
                "status": "success",
                "replayed": 1,
                "skipped": 1,
                "failed": 0,
                "remaining": 0,
            },
        },
    ]
    (mem / "maintenance_policy_log.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
        encoding="utf-8",
    )

    cfg = {
        "workspace_dir": ws,
        "memory_dir": mem,
        "settings": {"memory": {"monitoring": {"enabled": True}}},
    }
    m = MemoryMonitoring(cfg)
    snap = m.status()
    sr = snap["maintenance_policy_stats"]["shadow_replay"]
    assert sr["runs"] == 2
    assert sr["ok_runs"] == 1
    assert sr["partial_runs"] == 1
    assert sr["replayed_total"] == 3
    assert sr["failed_total"] == 1
    assert sr["remaining_last"] == 0


def test_shadow_replay_failure_emits_alert() -> None:
    ws = Path(tempfile.mkdtemp(prefix="monitor_shadow_alert_"))
    mem = ws / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (ws / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    (mem / "learning_task_log.jsonl").write_text("", encoding="utf-8")
    (mem / "memory_usage_log.jsonl").write_text("", encoding="utf-8")
    (mem / "auto_memory_log.json").write_text(json.dumps({"entries": []}), encoding="utf-8")

    rows = [
        {
            "timestamp": "2026-04-21T00:00:00",
            "action": "shadow_replay_spool",
            "result": {
                "status": "partial",
                "replayed": 0,
                "skipped": 0,
                "failed": 2,
                "remaining": 2,
            },
        }
    ]
    (mem / "maintenance_policy_log.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
        encoding="utf-8",
    )

    cfg = {
        "workspace_dir": ws,
        "memory_dir": mem,
        "settings": {
            "memory": {
                "monitoring": {
                    "enabled": True,
                    "alerts": {
                        "enabled": True,
                        "alert_cooldown_hours": 1,
                        "no_new_memory_hours": 6,
                        "shadow_replay_failed_threshold": 1,
                        "shadow_replay_remaining_threshold": 20,
                    },
                    "slo": {
                        "capture_rate_min": 0.0,
                        "injection_rate_min": 0.0,
                        "duplicate_drop_rate_max": 1.0,
                        "backup_success_rate_min": 0.0,
                        "restore_drill_success_rate_min": 0.0,
                    },
                }
            }
        },
    }
    m = MemoryMonitoring(cfg)
    snap = m.status()
    codes = [str(x.get("code", "")) for x in snap.get("alerts_emitted", []) if isinstance(x, dict)]
    assert "shadow_replay_failed" in codes


def test_shadow_runtime_stats_and_slo_fields_present() -> None:
    ws = Path(tempfile.mkdtemp(prefix="monitor_shadow_runtime_"))
    mem = ws / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (ws / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    (mem / "learning_task_log.jsonl").write_text("", encoding="utf-8")
    (mem / "memory_usage_log.jsonl").write_text("", encoding="utf-8")
    (mem / "auto_memory_log.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    (mem / "maintenance_policy_log.jsonl").write_text("", encoding="utf-8")

    shadow_dir = mem / "security" / "shadow_data"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    (shadow_dir / "shadow_spool.jsonl").write_text(
        '{"spool_id":"a","replayed":false}\n',
        encoding="utf-8",
    )
    (shadow_dir / "shadow_verify.jsonl").write_text(
        '{"ts":"2026-04-21T00:00:00","ok":true}\n',
        encoding="utf-8",
    )

    cfg = {
        "workspace_dir": ws,
        "memory_dir": mem,
        "settings": {"memory": {"monitoring": {"enabled": True}}},
    }
    m = MemoryMonitoring(cfg)
    snap = m.status()
    assert "shadow_runtime_stats" in snap
    assert "actuals" in snap.get("slo", {})
    assert "shadow_spool_pending" in snap["slo"]["checks"]


def test_shadow_startup_integrity_aggregated_window() -> None:
    ws = Path(tempfile.mkdtemp(prefix="monitor_shadow_startup_integrity_"))
    mem = ws / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (ws / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    (mem / "learning_task_log.jsonl").write_text("", encoding="utf-8")
    (mem / "memory_usage_log.jsonl").write_text("", encoding="utf-8")
    (mem / "auto_memory_log.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    (mem / "maintenance_policy_log.jsonl").write_text("", encoding="utf-8")
    shadow_dir = mem / "security" / "shadow_data"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "event_id": "a",
            "seq": 1,
            "ts": "2026-04-22T00:00:00+00:00",
            "event_type": "mode",
            "action": "protect",
            "source": "shadow:startup_integrity",
            "mode": "active",
            "ok": True,
            "metadata": {"signature": "ok"},
        },
        {
            "event_id": "b",
            "seq": 2,
            "ts": "2026-04-22T00:10:00+00:00",
            "event_type": "mode",
            "action": "protect",
            "source": "shadow:startup_integrity",
            "mode": "active",
            "ok": False,
            "metadata": {"signature": "fail:checkpoint_mismatch"},
        },
        {
            "event_id": "c",
            "seq": 3,
            "ts": "2026-04-22T00:20:00+00:00",
            "event_type": "mode",
            "action": "protect",
            "source": "shadow:startup_integrity",
            "mode": "active",
            "ok": False,
            "metadata": {"signature": "fail:checkpoint_mismatch"},
        },
    ]
    (shadow_dir / "shadow_events.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
        encoding="utf-8",
    )
    cfg = {
        "workspace_dir": ws,
        "memory_dir": mem,
        "settings": {
            "memory": {
                "monitoring": {
                    "enabled": True,
                    "shadow_startup_integrity_window_hours": 999999,
                }
            }
        },
    }
    m = MemoryMonitoring(cfg)
    snap = m.status()
    si = snap["shadow_startup_integrity"]
    assert int(si.get("events_total", 0)) == 3
    assert int(si.get("ok_count", 0)) == 1
    assert int(si.get("fail_count", 0)) == 2
    assert int(si.get("distinct_signatures", 0)) >= 2


def test_shadow_startup_integrity_alerts_emitted_on_threshold() -> None:
    ws = Path(tempfile.mkdtemp(prefix="monitor_shadow_startup_integrity_alert_"))
    mem = ws / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (ws / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    (mem / "learning_task_log.jsonl").write_text("", encoding="utf-8")
    (mem / "memory_usage_log.jsonl").write_text("", encoding="utf-8")
    (mem / "maintenance_policy_log.jsonl").write_text("", encoding="utf-8")
    (mem / "auto_memory_log.json").write_text(
        json.dumps({"entries": [{"timestamp": "2026-04-22T00:00:00+00:00", "status": "success"}]}),
        encoding="utf-8",
    )
    shadow_dir = mem / "security" / "shadow_data"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(12):
        rows.append(
            {
                "event_id": f"f{i}",
                "seq": i + 1,
                "ts": "2026-04-22T00:10:00+00:00",
                "event_type": "mode",
                "action": "protect",
                "source": "shadow:startup_integrity",
                "mode": "active",
                "ok": False,
                "metadata": {"signature": "fail:checkpoint_mismatch"},
            }
        )
    for i in range(8):
        rows.append(
            {
                "event_id": f"o{i}",
                "seq": 100 + i + 1,
                "ts": "2026-04-22T00:20:00+00:00",
                "event_type": "mode",
                "action": "protect",
                "source": "shadow:startup_integrity",
                "mode": "active",
                "ok": True,
                "metadata": {"signature": "ok"},
            }
        )
    (shadow_dir / "shadow_events.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
        encoding="utf-8",
    )
    cfg = {
        "workspace_dir": ws,
        "memory_dir": mem,
        "settings": {
            "memory": {
                "monitoring": {
                    "enabled": True,
                    "shadow_startup_integrity_window_hours": 999999,
                    "alerts": {
                        "enabled": True,
                        "alert_cooldown_hours": 1,
                        "no_new_memory_hours": 999999,
                        "shadow_startup_integrity_min_events": 20,
                        "shadow_startup_integrity_fail_count_threshold": 10,
                        "shadow_startup_integrity_fail_ratio_threshold": 0.50,
                    },
                    "slo": {
                        "capture_rate_min": 0.0,
                        "injection_rate_min": 0.0,
                        "duplicate_drop_rate_max": 1.0,
                        "backup_success_rate_min": 0.0,
                        "restore_drill_success_rate_min": 0.0,
                    },
                }
            }
        },
    }
    m = MemoryMonitoring(cfg)
    snap = m.status()
    codes = [str(x.get("code", "")) for x in snap.get("alerts_emitted", []) if isinstance(x, dict)]
    assert "shadow_startup_integrity_fail_high" in codes
    assert "shadow_startup_integrity_ratio_high" in codes
