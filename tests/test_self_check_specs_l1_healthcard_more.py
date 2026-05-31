from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.maintenance.self_check import check_specs as cs


def _core(tmp_path: Path) -> SimpleNamespace:
    memory = tmp_path / "mem"
    memory.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        config={
            "memory_dir": str(memory),
            "settings": {"memory": {"self_check": {"health_card_compare_target": "baseline"}}},
        },
    )


def test_l1_health_card_baseline_missing_signature(tmp_path: Path) -> None:
    core = _core(tmp_path)
    mem = Path(core.config["memory_dir"])
    (mem / "health_card_baseline.json").write_text("{}", encoding="utf-8")
    out = cs._check_l1_health_card_diff(core, {})
    assert out["status"] == cs.STATUS_FAIL
    assert "signature missing" in out["message"]


def test_l1_health_card_baseline_signature_mismatch(tmp_path: Path) -> None:
    core = _core(tmp_path)
    mem = Path(core.config["memory_dir"])
    card = mem / "health_card_baseline.json"
    card.write_text('{"a":1}', encoding="utf-8")
    (mem / "health_card_baseline.sha256").write_text("deadbeef", encoding="utf-8")
    out = cs._check_l1_health_card_diff(core, {})
    assert out["status"] == cs.STATUS_FAIL
    assert "signature mismatch" in out["message"]


def test_l1_health_card_baseline_drift_paths(monkeypatch, tmp_path: Path) -> None:
    core = _core(tmp_path)
    mem = Path(core.config["memory_dir"])
    card = mem / "health_card_baseline.json"
    payload = {"v": 1}
    card.write_text(json.dumps(payload), encoding="utf-8")
    (mem / "health_card_baseline.sha256").write_text(hashlib.sha256(card.read_bytes()).hexdigest(), encoding="utf-8")

    import ms8.engine_core.maintenance.self_check.reporter as reporter

    monkeypatch.setattr(reporter, "build_health_card", lambda *_a, **_k: {"v": 2})
    monkeypatch.setattr(reporter, "_diff_health_card", lambda *_a, **_k: {"summary": {"critical": 1, "warning": 0}, "diffs": ["x"]})
    out_critical = cs._check_l1_health_card_diff(core, {})
    assert out_critical["status"] == cs.STATUS_FAIL
    assert "critical drift" in out_critical["message"]

    monkeypatch.setattr(reporter, "_diff_health_card", lambda *_a, **_k: {"summary": {"critical": 0, "warning": 1}, "diffs": ["w"]})
    out_warn_only = cs._check_l1_health_card_diff(core, {})
    assert out_warn_only["status"] == cs.STATUS_PASS
    assert "warning-only" in out_warn_only["message"]

    monkeypatch.setattr(reporter, "_diff_health_card", lambda *_a, **_k: {"summary": {"critical": 0, "warning": 0}, "diffs": []})
    out_ok = cs._check_l1_health_card_diff(core, {})
    assert out_ok["status"] == cs.STATUS_PASS
    assert "check passed" in out_ok["message"]
