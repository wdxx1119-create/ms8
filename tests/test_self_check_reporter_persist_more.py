from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine_core.maintenance.self_check import reporter


def test_detect_performance_regression_ignores_invalid_history(tmp_path: Path) -> None:
    history = tmp_path / "history"
    history.mkdir(parents=True, exist_ok=True)
    (history / "a.json").write_text("{bad", encoding="utf-8")
    (history / "b.json").write_text(json.dumps({"results": ["bad"]}), encoding="utf-8")
    report = {"results": [{"check_id": "c1", "duration_ms": 10.0}]}
    out = reporter._detect_performance_regression(history, report, lookback=10)
    assert out["count"] == 0
    assert "performance_regression" not in report["results"][0]


def test_cleanup_history_removes_old_files_by_age(tmp_path: Path) -> None:
    history = tmp_path / "history"
    history.mkdir(parents=True, exist_ok=True)
    old = history / "old.json"
    new = history / "new.json"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=90)).timestamp()
    new_ts = datetime.now(timezone.utc).timestamp()
    old.touch()
    new.touch()
    # set explicit mtimes
    import os

    os.utime(old, (old_ts, old_ts))
    os.utime(new, (new_ts, new_ts))
    meta = reporter._cleanup_history(history, keep_days=30, keep_max=100)
    assert meta["removed"] >= 1
    assert new.exists()


def test_emit_macos_notifications_error_branch(monkeypatch) -> None:
    monkeypatch.setattr(reporter.sys, "platform", "darwin")

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError("osascript-fail")

    monkeypatch.setattr(reporter.subprocess, "run", _boom)
    out = reporter._emit_macos_notifications({"emitted": [{"check_id": "x", "status": "warn"}]})
    assert out["status"] == "error"
    assert out["count"] == 1


def test_persist_report_fills_missing_sections_and_writes_daily(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "ms8.engine_core.maintenance.self_repair.repair_audit.summarize_repair_7d",
        lambda _memory_dir, days=7: {"window_days": days, "total": 0},
    )
    # disable osascript side effects
    monkeypatch.setattr(reporter.sys, "platform", "linux")

    report = {
        "requested_level": "L4",
        "started_at": "2026-05-19T00:00:00+00:00",
        "finished_at": "2026-05-19T00:00:02+00:00",
        "status": "ok",
        "summary": {"total": 1, "pass": 1, "warn": 0, "fail": 0, "error": 0, "exit_code": 0},
        "results": [{"check_id": "c1", "status": "pass", "level": "L4", "domain": "memory", "duration_ms": 10}],
    }
    out = reporter.persist_report(tmp_path, report, keep_days=1, keep_max=5, cooldown_hours=1, max_alerts_per_day=1)
    assert Path(out["latest_json"]).exists()
    assert Path(out["latest_md"]).exists()
    assert Path(out["history_file"]).exists()
    latest = json.loads(Path(out["latest_json"]).read_text(encoding="utf-8"))
    assert "domain_summary" in latest
    assert "maturity_gate" in latest
    assert "repair_summary" in latest
    assert (tmp_path / "reports" / "self_check_daily_summary.md").exists()


def test_write_daily_summary_no_latest_is_safe(tmp_path: Path) -> None:
    # no latest file present
    reporter._write_daily_summary(tmp_path)
    # should not create summary if latest missing/error
    assert not (tmp_path / "reports" / "self_check_daily_summary.md").exists()
