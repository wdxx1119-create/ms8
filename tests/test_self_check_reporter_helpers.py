from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check import reporter


def test_severity_order_values() -> None:
    assert reporter._severity_order("error") > reporter._severity_order("fail")
    assert reporter._severity_order("fail") > reporter._severity_order("warn")
    assert reporter._severity_order("warn") > reporter._severity_order("pass")
    assert reporter._severity_order("unknown") == 0


def test_domain_summary_unknown_status_counts_as_error() -> None:
    out = reporter._domain_summary(
        {"results": [{"domain": "memory", "status": "pass"}, {"domain": "memory", "status": "weird"}]}
    )
    assert out["memory"]["total"] == 2
    assert out["memory"]["pass"] == 1
    assert out["memory"]["error"] == 1


def test_p95_empty_and_non_empty() -> None:
    assert reporter._p95([]) == 0.0
    assert reporter._p95([1, 2, 3, 4, 5]) >= 4.0


def test_detect_performance_regression_marks_row(tmp_path: Path) -> None:
    history = tmp_path / "history"
    history.mkdir(parents=True, exist_ok=True)
    for i in range(4):
        (history / f"h{i}.json").write_text(
            json.dumps({"results": [{"check_id": "c1", "duration_ms": 10 + i}]}, ensure_ascii=False),
            encoding="utf-8",
        )
    report = {"results": [{"check_id": "c1", "duration_ms": 100.0}]}
    out = reporter._detect_performance_regression(history, report, lookback=10)
    assert out["count"] == 1
    assert report["results"][0]["performance_regression"] is True


def test_cleanup_history_limits_keep_max(tmp_path: Path) -> None:
    history = tmp_path / "history"
    history.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (history / f"{i}.json").write_text("{}", encoding="utf-8")
    meta = reporter._cleanup_history(history, keep_days=365, keep_max=2)
    assert meta["remaining"] <= 2
    assert meta["removed"] >= 3


def test_load_repair_summary_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "ms8.engine_core.maintenance.self_repair.repair_audit.summarize_repair_7d",
        lambda _memory_dir, days=7: {"window_days": days, "total": 0},
    )
    out = reporter._load_repair_summary(tmp_path)
    assert out["status"] == "missing"
    assert out["window_7d"]["total"] == 0
