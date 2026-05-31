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
                "self_check": {"heartbeat_path": str(tmp_path / "hb")},
                "monitoring": {"alerts": {"self_check_stale_hours": 1}},
            }
        },
    }
    return MemoryMonitoring(cfg)


def test_admission_route_distribution_legacy_and_current(tmp_path: Path) -> None:
    mon = _mk_monitor(tmp_path)
    p = tmp_path / "memory" / "auto_memory_pipeline.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"trace": [{"stage": "admission", "payload": {"route": "accepted"}}]},
        {"trace": {"events": [{"stage": "admission", "detail": {"route": "pending_review"}}]}},
        {"admission": {"route": "rejected"}},
        {"trace": [{"stage": "noop"}]},
    ]
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    dist = mon._admission_route_distribution(limit=50)
    assert dist["accepted"] == 1
    assert dist["pending_review"] == 1
    assert dist["rejected"] == 1


def test_maintenance_policy_stats_shadow_replay(tmp_path: Path) -> None:
    mon = _mk_monitor(tmp_path)
    p = tmp_path / "memory" / "maintenance_policy_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"action": "x", "result": {"status": "ok"}},
        {"action": "shadow_replay_spool", "timestamp": "2026-01-01T00:00:00+00:00", "result": {"status": "partial", "replayed": 2, "skipped": 1, "failed": 1, "remaining": 3}},
        {"action": "shadow_replay_spool", "timestamp": "2026-01-02T00:00:00+00:00", "result": {"status": "failed", "replayed": 0, "skipped": 0, "failed": 2, "remaining": 5}},
    ]
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    out = mon._maintenance_policy_stats(limit=50)
    assert out["total_runs"] == 3
    assert out["success_runs"] == 1
    assert out["failed_runs"] == 1
    sr = out["shadow_replay"]
    assert sr["runs"] == 2
    assert sr["partial_runs"] == 1
    assert sr["failed_runs"] == 1
    assert sr["replayed_total"] == 2
    assert sr["remaining_last"] == 5


def test_self_check_stats_stale_and_parse_fail(tmp_path: Path) -> None:
    mon = _mk_monitor(tmp_path)
    reports = tmp_path / "memory" / "reports"
    hist = reports / "self_check_history"
    reports.mkdir(parents=True, exist_ok=True)
    hist.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        (hist / f"r{i}.json").write_text("{}", encoding="utf-8")
    latest = reports / "self_check_latest.json"
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    latest.write_text(
        json.dumps(
            {
                "summary": {"exit_code": 1},
                "finished_at": old.isoformat(),
                "requested_level": "L4",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    hb = tmp_path / "hb"
    hb.write_text("ok", encoding="utf-8")
    # make heartbeat stale
    ts = (datetime.now(timezone.utc) - timedelta(hours=3)).timestamp()
    hb.touch()
    import os

    os.utime(hb, (ts, ts))

    out = mon._self_check_stats()
    assert out["latest_exists"] is True
    assert out["history_count"] == 2
    assert out["latest_exit_code"] == 1
    assert out["latest_level"] == "L4"
    assert out["stale"] is True

    # parse-fail branch
    latest.write_text("{bad-json}", encoding="utf-8")
    out2 = mon._self_check_stats()
    assert out2["latest_exists"] is True
