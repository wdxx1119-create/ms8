from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check import reporter


def test_update_alert_state_emits_and_resets_pass(tmp_path: Path) -> None:
    state_path = tmp_path / "alert_state.json"
    report_warn = {"results": [{"check_id": "c1", "status": "warn"}]}
    out1 = reporter._update_alert_state(report_warn, state_path, cooldown_hours=1, max_per_day=3)
    assert out1["emitted"]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert "c1:warn" in saved

    report_pass = {"results": [{"check_id": "c1", "status": "pass"}]}
    out2 = reporter._update_alert_state(report_pass, state_path, cooldown_hours=1, max_per_day=3)
    assert out2["emitted"] == []
    saved2 = json.loads(state_path.read_text(encoding="utf-8"))
    assert "c1:warn" not in saved2


def test_update_alert_state_mute_by_daily_limit(tmp_path: Path) -> None:
    state_path = tmp_path / "alert_state.json"
    report = {"results": [{"check_id": "c2", "status": "fail"}]}
    # first emit
    reporter._update_alert_state(report, state_path, cooldown_hours=0, max_per_day=1)
    # second should mute under daily limit
    out = reporter._update_alert_state(report, state_path, cooldown_hours=0, max_per_day=1)
    assert out["emitted"] == []
    assert out["muted"]


def test_emit_macos_notifications_skip_paths(monkeypatch) -> None:
    assert reporter._emit_macos_notifications({"emitted": []})["status"] == "skipped"
    monkeypatch.setattr(reporter.sys, "platform", "linux")
    out = reporter._emit_macos_notifications({"emitted": [{"check_id": "x", "status": "warn"}]})
    assert out["status"] == "skipped"
    assert out["reason"] == "non_macos"


def test_load_latest_missing_and_error(tmp_path: Path) -> None:
    out_missing = reporter.load_latest(tmp_path)
    assert out_missing["status"] == "missing"

    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "self_check_latest.json").write_text("{bad json", encoding="utf-8")
    out_error = reporter.load_latest(tmp_path)
    assert out_error["status"] == "error"


def test_list_history_respects_level_filter(tmp_path: Path) -> None:
    history = tmp_path / "reports" / "self_check_history"
    history.mkdir(parents=True, exist_ok=True)
    (history / "a.json").write_text(
        json.dumps(
            {
                "requested_level": "L1",
                "status": "pass",
                "summary": {"pass": 1, "warn": 0, "fail": 0, "error": 0, "exit_code": 0},
                "finished_at": "2026-05-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (history / "b.json").write_text(
        json.dumps(
            {
                "requested_level": "L4",
                "status": "warn",
                "summary": {"pass": 0, "warn": 1, "fail": 0, "error": 0, "exit_code": 1},
                "finished_at": "2026-05-02T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    out = reporter.list_history(tmp_path, limit=10, level="L4")
    assert len(out) == 1
    assert out[0]["level"] == "L4"

