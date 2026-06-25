from __future__ import annotations

import json
from pathlib import Path

from ms8 import runtime


def _stub_paths(root: Path) -> dict[str, Path]:
    health = root / "health"
    memory = root / "memory"
    health.mkdir(parents=True, exist_ok=True)
    memory.mkdir(parents=True, exist_ok=True)
    quarantine = memory / "noncanonical_quarantine.jsonl"
    quarantine.write_text("", encoding="utf-8")
    fallback_log = health / "governance_fallback_log.jsonl"
    fallback_log.write_text("", encoding="utf-8")
    compression = memory / "compression_state.json"
    compression.write_text("{}", encoding="utf-8")
    config_file = root / "config.json"
    config_file.write_text("{}", encoding="utf-8")
    return {
        "root": root,
        "health": health,
        "memory": memory,
        "quarantine": quarantine,
        "compression_state": compression,
        "config_file": config_file,
    }


def test_governance_report_includes_policy_attack_samples(monkeypatch, tmp_path: Path) -> None:
    paths = _stub_paths(tmp_path)
    latest = paths["health"] / "policy_attack_samples_latest.json"
    latest.write_text(
        json.dumps(
            {
                "ok": False,
                "total_cases": 3,
                "passed_cases": 2,
                "failed_cases": 1,
                "updated_at": "2026-05-30T00:00:00+00:00",
                "age_hours": 1.0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(runtime, "read_memories", lambda: [])
    monkeypatch.setattr(runtime, "validate_record", lambda r: (True, ""))
    monkeypatch.setattr(runtime, "run_engine_self_check", lambda level="L4": {"status": "ok"})
    monkeypatch.setattr(
        runtime,
        "get_engine_shadow_status",
        lambda: {"enabled": True, "mode": "active", "sealed": False, "manifest": {"reason": ""}},
    )
    monkeypatch.setattr(
        runtime,
        "get_engine_monitoring_status",
        lambda: {
            "rates": {"capture_rate": 1.0, "auto_total_entries": 40},
            "rates_v2": {"eligible_events": 40},
            "slo": {"targets": {"capture_rate_min": 0.85, "capture_rate_min_samples": 30}},
            "slo_v2_preview": {"all_ok": True},
            "compression_freshness": {"hours_since_last": 1.0},
        },
    )

    report = runtime.get_governance_report()
    pas = report["policy_attack_samples"]
    assert pas["present"] is True
    assert pas["failed_cases"] == 1
    assert pas["initialized"] is True
    assert report["health_domains"]["retrieval_safety_health"] == "red"
    assert "policy_attack_samples_failed" in report["health_domains"]["overall_reasons"]


def test_governance_report_bootstraps_policy_attack_baseline(monkeypatch, tmp_path: Path) -> None:
    paths = _stub_paths(tmp_path)

    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(runtime, "read_memories", lambda: [])
    monkeypatch.setattr(runtime, "validate_record", lambda r: (True, ""))
    monkeypatch.setattr(runtime, "run_engine_self_check", lambda level="L4": {"status": "ok"})
    monkeypatch.setattr(
        runtime,
        "get_engine_shadow_status",
        lambda: {"enabled": True, "mode": "active", "sealed": False, "manifest": {"reason": ""}},
    )
    monkeypatch.setattr(
        runtime,
        "get_engine_monitoring_status",
        lambda: {
            "rates": {"capture_rate": 1.0, "auto_total_entries": 40},
            "rates_v2": {"eligible_events": 40},
            "slo": {"targets": {"capture_rate_min": 0.85, "capture_rate_min_samples": 30}},
            "slo_v2_preview": {"all_ok": True},
            "compression_freshness": {"hours_since_last": 1.0},
        },
    )

    report = runtime.get_governance_report()
    pas = report["policy_attack_samples"]
    assert pas["present"] is True
    assert pas["ok"] is True
    assert pas["failed_cases"] == 0
    assert pas["initialized"] is False
    assert (paths["health"] / "policy_attack_samples_latest.json").exists()


def test_governance_report_shadow_startup_integrity_failure_degrades_runtime(monkeypatch, tmp_path: Path) -> None:
    paths = _stub_paths(tmp_path)

    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(runtime, "read_memories", lambda: [])
    monkeypatch.setattr(runtime, "validate_record", lambda r: (True, ""))
    monkeypatch.setattr(
        runtime,
        "run_engine_self_check",
        lambda level="L4": {
            "status": "ok",
            "domain_summary": {"security": {"total": 1, "pass": 1, "warn": 0, "fail": 0, "error": 0}},
        },
    )
    monkeypatch.setattr(
        runtime,
        "get_engine_shadow_status",
        lambda: {
            "enabled": True,
            "mode": "sealed",
            "sealed": True,
            "seal_level": "hard",
            "manifest": {
                "reason": "startup_integrity_failed",
                "manifest_signature_valid": False,
                "startup_findings": ["manifest_signature_invalid"],
            },
        },
    )
    monkeypatch.setattr(
        runtime,
        "get_engine_monitoring_status",
        lambda: {
            "rates": {"capture_rate": 1.0, "auto_total_entries": 40},
            "rates_v2": {"eligible_events": 40},
            "slo": {"targets": {"capture_rate_min": 0.85, "capture_rate_min_samples": 30}},
            "slo_v2_preview": {"all_ok": True},
            "compression_freshness": {"hours_since_last": 1.0},
        },
    )

    report = runtime.get_governance_report()
    shadow = report["shadow_runtime"]
    assert shadow["status"] == "degraded"
    assert shadow["reason"] == "startup_integrity_failed"
    assert report["health_domains"]["runtime_health"] == "red"
    assert report["health_domains"]["security_integrity_health"] == "red"
    assert "shadow_startup_integrity_failed" in report["health_domains"]["overall_reasons"]
