from __future__ import annotations

from pathlib import Path

from ms8 import doctor


def _baseline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setattr(doctor, "ensure_runtime_dirs", lambda: {"root": tmp_path / ".ms8"})
    monkeypatch.setattr(doctor, "count_memories", lambda: 1)

    class _Usage:
        total = 10
        used = 1
        free = 9 * (1024**3)

    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda _p: _Usage())
    monkeypatch.setattr(doctor, "engine_status", lambda: {"mode": "ms8_core", "available": True})
    monkeypatch.setattr(
        doctor,
        "run_engine_self_check",
        lambda level="L4": {
            "schema_version": "1.0",
            "status": "ok",
            "summary": {"total": 1, "pass": 1, "warn": 0, "fail": 0, "error": 0, "exit_code": 0},
            "results": [{"check_id": "x", "status": "pass"}],
            "domain_summary": {"memory": {"total": 1, "pass": 1, "warn": 0, "fail": 0, "error": 0, "pass_rate": 1.0}},
            "maturity_gate": {"memory_ready": True, "security_ready": True, "connect_ready": True, "overall_ready": True},
        },
    )
    monkeypatch.setattr(doctor, "get_engine_llm_status", lambda: {"available": True, "providers": {}})
    monkeypatch.setattr(
        doctor,
        "get_llm_status_runtime",
        lambda: {
            "recommended_mode": "cloud",
            "effective_mode_ladder": {"mode": "cloud_only", "effective_available": True},
            "configured": {"provider_order_chat": ["openai"], "provider_order_embedding": ["openai"]},
        },
    )
    monkeypatch.setattr(doctor, "get_expression_router_status", lambda: {"total_samples": 0, "mode_counts": {}})
    monkeypatch.setattr(
        doctor,
        "get_capability_reachability_report",
        lambda top_unreachable=10: {"reachable_ratio": 1.0, "referenced_methods": 1, "public_methods_total": 1, "unreachable_methods": 0},
    )
    monkeypatch.setattr(
        doctor,
        "_agent_native_status",
        lambda: {"policy": "PRESENT", "permission_profile": "DEFAULT_SAFE", "task_files": "install=P, ops=P, usage=P", "agent_native_status": "OK"},
    )
    monkeypatch.setattr(doctor, "has_recent_activity", lambda _w=3600: True)


def test_memory_quality_fallback_degraded_on_capture_and_compression(monkeypatch, capsys, tmp_path: Path) -> None:
    _baseline(monkeypatch, tmp_path)
    monkeypatch.setattr(
        doctor,
        "get_engine_monitoring_status",
        lambda: {
            "enabled": True,
            "alerts": [],
            "rates": {"capture_rate": 0.1, "auto_total_entries": 200},
            "slo": {"checks": {"capture_rate": False}, "targets": {"capture_rate_min": 0.85, "capture_rate_min_samples": 30}},
            "compression_freshness": {"hours_since_last": 260.0},
        },
    )
    monkeypatch.setattr(
        doctor,
        "get_governance_report",
        lambda: {
            "noncanonical_records": 0,
            "schema_invalid_count": 0,
            "fallback_write_count": 0,
            "fallback_total_count": 0,
            "duplicate_groups": 0,
            "pending_review": 0,
            "pending_review_oldest_hours": 0.0,
            "self_check_status": "ok",
            "trend": {"window_24h": {"samples": 1, "risk": "green", "delta": {}}, "window_7d": {"samples": 1, "risk": "green", "delta": {}}},
        },
    )
    code = doctor.run_doctor()
    out = capsys.readouterr().out
    assert code == 1
    assert "memory_quality_health: degraded" in out
    assert "Overall: degraded" in out


def test_memory_quality_fallback_warn_and_allow_degraded(monkeypatch, capsys, tmp_path: Path) -> None:
    _baseline(monkeypatch, tmp_path)
    monkeypatch.setenv("MS8_DOCTOR_ALLOW_DEGRADED", "1")
    monkeypatch.setattr(doctor, "engine_status", lambda: {"mode": "ms8_core", "available": False})
    monkeypatch.setattr(
        doctor,
        "get_engine_monitoring_status",
        lambda: {
            "enabled": True,
            "alerts": [],
            "rates": {"capture_rate": 0.6, "auto_total_entries": 20},
            "slo": {"checks": {"capture_rate": True}, "targets": {"capture_rate_min": 0.85, "capture_rate_min_samples": 30}},
            "compression_freshness": {"hours_since_last": 170.0},
        },
    )
    monkeypatch.setattr(
        doctor,
        "get_governance_report",
        lambda: {
            "noncanonical_records": 0,
            "schema_invalid_count": 0,
            "fallback_write_count": 0,
            "fallback_total_count": 0,
            "duplicate_groups": 0,
            "pending_review": 0,
            "pending_review_oldest_hours": 0.0,
            "self_check_status": "ok",
            "trend": {"window_24h": {"samples": 1, "risk": "green", "delta": {}}, "window_7d": {"samples": 1, "risk": "green", "delta": {}}},
        },
    )
    code = doctor.run_doctor()
    out = capsys.readouterr().out
    assert code == 0
    assert "runtime_health: degraded" in out
    assert "memory_quality_health: warn" in out
