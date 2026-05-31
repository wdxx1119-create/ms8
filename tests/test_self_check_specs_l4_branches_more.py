from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _Core:
    def __init__(self, memory_dir: Path) -> None:
        self.config = {
            "memory_dir": str(memory_dir),
            "settings": {"memory": {"connect": {}}},
        }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_connect_root_and_load_json_helpers(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    root = cs._connect_root(core)
    assert root.name == "connect"

    d = tmp_path / "a.json"
    _write_json(d, {"ok": True})
    assert cs._load_json(d) == {"ok": True}

    # non-dict payload should normalize to {}
    d.write_text("[]", encoding="utf-8")
    assert cs._load_json(d) == {}

    # invalid payload should not raise
    d.write_text("{", encoding="utf-8")
    assert cs._load_json(d) == {}


def test_l4_capture_trend_warn_paths(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    out_missing = cs._check_l4_capture_trend(core, {})
    assert out_missing["status"] == "warn"

    log_path = tmp_path / "auto_memory_log.json"
    log_path.write_text("{", encoding="utf-8")
    out_unreadable = cs._check_l4_capture_trend(core, {})
    assert out_unreadable["status"] == "warn"

    _write_json(log_path, {"entries": []})
    out_empty = cs._check_l4_capture_trend(core, {})
    assert out_empty["status"] == "warn"


def test_l4_capture_trend_fail_and_declining(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    now = datetime.now(timezone.utc)

    # Low average success ratio -> fail.
    low_entries: list[dict] = []
    for i in range(4):
        day_ts = (now - timedelta(days=4 - i)).isoformat()
        low_entries.extend(
            [
                {"timestamp": day_ts, "status": "success"},
                {"timestamp": day_ts, "status": "rejected"},
                {"timestamp": day_ts, "status": "duplicate"},
                {"timestamp": day_ts, "status": "duplicate"},
            ]
        )
    _write_json(tmp_path / "auto_memory_log.json", {"entries": low_entries})
    out_low = cs._check_l4_capture_trend(core, {})
    assert out_low["status"] == "fail"

    # Declining trend but not low average -> warn.
    decline_entries: list[dict] = []
    # early high days
    for i in range(2):
        day_ts = (now - timedelta(days=4 - i)).isoformat()
        decline_entries.extend(
            [
                {"timestamp": day_ts, "status": "success"},
                {"timestamp": day_ts, "status": "success"},
                {"timestamp": day_ts, "status": "success"},
                {"timestamp": day_ts, "status": "success"},
            ]
        )
    # later lower days
    for i in range(2):
        day_ts = (now - timedelta(days=2 - i)).isoformat()
        decline_entries.extend(
            [
                {"timestamp": day_ts, "status": "success"},
                {"timestamp": day_ts, "status": "duplicate"},
                {"timestamp": day_ts, "status": "duplicate"},
                {"timestamp": day_ts, "status": "duplicate"},
            ]
        )
    _write_json(tmp_path / "auto_memory_log.json", {"entries": decline_entries})
    out_decline = cs._check_l4_capture_trend(core, {})
    assert out_decline["status"] == "warn"


def test_l4_injection_effectiveness_all_branches(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    now = datetime.now(timezone.utc).isoformat()

    out_missing = cs._check_l4_injection_effectiveness(core, {})
    assert out_missing["status"] == "warn"

    usage = tmp_path / "memory_usage_log.jsonl"
    usage.write_text("\n", encoding="utf-8")
    out_no_samples = cs._check_l4_injection_effectiveness(core, {})
    assert out_no_samples["status"] == "pass"

    # Under sample path.
    usage.write_text(
        "\n".join(
            [
                json.dumps({"query": "q1", "used_in_answer": True, "timestamp": now}),
                json.dumps({"query": "q2", "used_in_answer": False, "injected_count": 0, "timestamp": now}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out_under = cs._check_l4_injection_effectiveness(core, {})
    assert out_under["status"] == "pass"
    assert out_under["details"]["under_sample"] is True

    # Enough samples, low ratio -> fail.
    low_rows = [json.dumps({"query": f"q{i}", "used_in_answer": i == 0, "timestamp": now}) for i in range(12)]
    usage.write_text("\n".join(low_rows) + "\n", encoding="utf-8")
    out_fail = cs._check_l4_injection_effectiveness(core, {})
    assert out_fail["status"] == "fail"

    # Enough samples, moderate ratio -> warn.
    moderate_rows = [json.dumps({"query": f"q{i}", "used_in_answer": i < 6, "timestamp": now}) for i in range(12)]
    usage.write_text("\n".join(moderate_rows) + "\n", encoding="utf-8")
    out_warn = cs._check_l4_injection_effectiveness(core, {})
    assert out_warn["status"] == "warn"


def test_l4_threshold_and_l5_notice_branches(tmp_path: Path) -> None:
    core = _Core(tmp_path)

    # Threshold unreadable.
    p = tmp_path / "threshold_suggestions_pending.json"
    p.write_text("{", encoding="utf-8")
    out_bad = cs._check_l4_threshold_suggestions(core, {})
    assert out_bad["status"] == "warn"

    # High backlog -> fail.
    now = datetime.now(timezone.utc)
    items = [{"status": "pending", "created_at": (now - timedelta(days=15)).isoformat()} for _ in range(20)]
    _write_json(p, {"items": items})
    out_fail = cs._check_l4_threshold_suggestions(core, {})
    assert out_fail["status"] == "fail"

    # Small queue -> pass.
    _write_json(p, {"items": [{"status": "accepted", "created_at": now.isoformat()}]})
    out_pass = cs._check_l4_threshold_suggestions(core, {})
    assert out_pass["status"] == "pass"

    # L5 branches.
    out_missing = cs._check_l5_llm_notice_state_health(core, {})
    assert out_missing["status"] == "pass"

    state = tmp_path / "health" / "llm_notice_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{", encoding="utf-8")
    out_unreadable = cs._check_l5_llm_notice_state_health(core, {})
    assert out_unreadable["status"] == "warn"

    state.write_text("[]", encoding="utf-8")
    out_invalid = cs._check_l5_llm_notice_state_health(core, {})
    assert out_invalid["status"] == "warn"

