from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check import reporter
from ms8.runtime import get_expression_router_status, get_runtime_dir, run_daily_learning


def test_maturity_gate_ignores_absent_domains() -> None:
    report = {
        "results": [
            {"domain": "memory", "status": "pass", "check_id": "m1"},
            {"domain": "memory", "status": "warn", "check_id": "m2"},
        ],
        "known_noncritical_failures": [],
    }
    gate = reporter._maturity_gate(report)
    assert gate["memory_ready"] is True
    assert gate["security_ready"] is True
    assert gate["connect_ready"] is True
    assert gate["overall_ready"] is True


def test_runtime_dir_defaults_to_ms8_home(monkeypatch) -> None:
    monkeypatch.delenv("MS8_HOME", raising=False)
    got = get_runtime_dir()
    assert got == Path.home() / ".ms8"


def test_expression_router_status_summary(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "ms8_home"
    memory = root / "memory"
    reports = memory / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MS8_HOME", str(root))

    decisions = [
        {"mode": "normal", "cooldown_applied": False, "profile_used": False, "reason": "threshold_normal"},
        {"mode": "light", "cooldown_applied": True, "profile_used": False, "reason": "cooldown_after_strong"},
        {"mode": "strong", "cooldown_applied": False, "profile_used": True, "reason": "threshold_strong"},
    ]
    with (reports / "expression_router_decisions.jsonl").open("w", encoding="utf-8") as f:
        for d in decisions:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    (memory / "expression_router_state.json").write_text(
        json.dumps({"current_round": 7, "last_mode": "light"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (memory / "expression_preference_profile.json").write_text(
        json.dumps({"evidence_count": 4}, ensure_ascii=False),
        encoding="utf-8",
    )

    out = get_expression_router_status(sample_size=10)
    assert out["total_samples"] == 3
    assert out["mode_counts"]["normal"] == 1
    assert out["mode_counts"]["light"] == 1
    assert out["mode_counts"]["strong"] == 1
    assert out["cooldown_applied_count"] == 1
    assert out["profile_used_count"] == 1
    assert out["current_round"] == 7
    assert out["last_mode"] == "light"
    assert out["profile_evidence_count"] == 4


def test_run_daily_learning_calls_core_trigger(monkeypatch) -> None:
    class DummyCore:
        called = False

        def trigger_daily_learning(self, date_str=None):
            self.called = True

    class DummyEngine:
        _core = DummyCore()

    monkeypatch.setattr("ms8.runtime._engine", lambda: DummyEngine())
    monkeypatch.setattr("ms8.runtime.maintenance_window_status", lambda: {"enabled": False})
    out = run_daily_learning()
    assert out["ok"] is True
    assert out["ran"] is True


def test_run_daily_learning_skips_in_maintenance_window(monkeypatch) -> None:
    monkeypatch.setattr(
        "ms8.runtime.maintenance_window_status",
        lambda: {"enabled": True, "pause_maintenance_writes": True},
    )
    out = run_daily_learning()
    assert out["ok"] is True
    assert out["ran"] is False
    assert out["reason"] == "maintenance_window"
