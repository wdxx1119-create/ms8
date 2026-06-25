from __future__ import annotations

from pathlib import Path

from ms8 import doctor


def _baseline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setattr(doctor, "ensure_runtime_dirs", lambda: {"root": tmp_path / ".ms8"})
    monkeypatch.setattr(doctor, "count_memories", lambda: 2)

    class _Usage:
        total = 10
        used = 3
        free = 7 * (1024**3)

    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda _p: _Usage())
    monkeypatch.setattr(doctor, "engine_status", lambda: {"mode": "ms8_core", "available": True})
    monkeypatch.setattr(
        doctor,
        "run_engine_self_check",
        lambda level="L4": {
            "schema_version": "1.0",
            "status": "ok",
            "summary": {"total": 2, "pass": 2, "warn": 0, "fail": 0, "error": 0, "exit_code": 0},
            "results": [{"check_id": "c1", "status": "pass"}, {"check_id": "c2", "status": "pass"}],
            "domain_summary": {"memory": {"total": 2, "pass": 2, "warn": 0, "fail": 0, "error": 0, "pass_rate": 1.0}},
            "maturity_gate": {"memory_ready": True, "security_ready": True, "connect_ready": True, "overall_ready": True},
        },
    )
    monkeypatch.setattr(doctor, "get_engine_llm_status", lambda: {"available": True, "providers": {}})
    monkeypatch.setattr(
        doctor,
        "get_engine_shadow_status",
        lambda: {"enabled": True, "mode": "active", "sealed": False, "manifest": {"reason": ""}},
    )
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


def test_doctor_warn_details_mismatch_line(monkeypatch, capsys, tmp_path: Path) -> None:
    _baseline(monkeypatch, tmp_path)
    monkeypatch.setattr(
        doctor,
        "run_engine_self_check",
        lambda level="L4": {
            "schema_version": "1.0",
            "status": "warning",
            "summary": {"total": 2, "pass": 1, "warn": 2, "fail": 0, "error": 0, "exit_code": 1},
            "results": [{"check_id": "x", "status": "pass"}, {"check_id": "y", "status": "warn"}],
            "domain_summary": {"memory": {"total": 2, "pass": 1, "warn": 1, "fail": 0, "error": 0, "pass_rate": 0.5}},
            "maturity_gate": {"memory_ready": True, "security_ready": True, "connect_ready": True, "overall_ready": True},
        },
    )
    monkeypatch.setattr(doctor, "get_engine_monitoring_status", lambda: {"enabled": True, "alerts": []})
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
            "self_check_status": "warn",
            "trend": {"window_24h": {"samples": 1, "risk": "yellow", "delta": {}}, "window_7d": {"samples": 1, "risk": "green", "delta": {}}},
            "health_domains": {"memory_quality_health": "yellow"},
        },
    )
    code = doctor.run_doctor()
    out = capsys.readouterr().out
    assert code == 1
    assert "self-check detail mismatch" in out
    assert "governance trend risk yellow" in out
    assert "Overall: warn" in out
    assert "memory_quality_health: warn" in out


def test_doctor_degraded_returns_one_when_runtime_degraded(monkeypatch, tmp_path: Path) -> None:
    _baseline(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "get_engine_monitoring_status", lambda: {"enabled": True, "alerts": []})
    monkeypatch.setattr(
        doctor,
        "get_governance_report",
        lambda: {
            "noncanonical_records": 0,
            "schema_invalid_count": 1,
            "fallback_write_count": 0,
            "fallback_total_count": 0,
            "duplicate_groups": 0,
            "pending_review": 0,
            "pending_review_oldest_hours": 0.0,
            "self_check_status": "error",
            "trend": {"window_24h": {"samples": 1, "risk": "red", "delta": {}}, "window_7d": {"samples": 1, "risk": "green", "delta": {}}},
            "health_domains": {
                "runtime_health": "green",
                "memory_quality_health": "red",
                "retrieval_safety_health": "red",
                "security_integrity_health": "green",
                "lifecycle_maintenance_health": "green",
            },
        },
    )
    code = doctor.run_doctor()
    assert code == 1


def test_doctor_trend_red_with_memory_only_drag_degrades(monkeypatch, capsys, tmp_path: Path) -> None:
    _baseline(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "get_engine_monitoring_status", lambda: {"enabled": True, "alerts": []})
    monkeypatch.setattr(
        doctor,
        "get_governance_report",
        lambda: {
            "noncanonical_records": 0,
            "schema_invalid_count": 0,
            "fallback_write_count": 0,
            "fallback_total_count": 38,
            "duplicate_groups": 12,
            "pending_review": 0,
            "pending_review_oldest_hours": 0.0,
            "self_check_status": "ok",
            "trend": {"window_24h": {"samples": 5, "risk": "red", "delta": {}}, "window_7d": {"samples": 9, "risk": "red", "delta": {}}},
            "health_domains": {
                "runtime_health": "green",
                "memory_quality_health": "red",
                "retrieval_safety_health": "green",
                "security_integrity_health": "green",
                "lifecycle_maintenance_health": "green",
            },
        },
    )
    code = doctor.run_doctor()
    out = capsys.readouterr().out
    assert code == 1
    assert "historical memory-quality drag" in out
    assert "Overall: degraded" in out


def test_doctor_trend_red_with_active_runtime_governance_risk_degrades(monkeypatch, capsys, tmp_path: Path) -> None:
    _baseline(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "get_engine_monitoring_status", lambda: {"enabled": True, "alerts": []})
    monkeypatch.setattr(
        doctor,
        "get_governance_report",
        lambda: {
            "noncanonical_records": 0,
            "schema_invalid_count": 0,
            "fallback_write_count": 0,
            "fallback_total_count": 38,
            "duplicate_groups": 12,
            "pending_review": 0,
            "pending_review_oldest_hours": 0.0,
            "self_check_status": "ok",
            "trend": {"window_24h": {"samples": 5, "risk": "red", "delta": {}}, "window_7d": {"samples": 9, "risk": "green", "delta": {}}},
            "health_domains": {
                "runtime_health": "green",
                "memory_quality_health": "red",
                "retrieval_safety_health": "red",
                "security_integrity_health": "green",
                "lifecycle_maintenance_health": "green",
            },
        },
    )
    code = doctor.run_doctor()
    out = capsys.readouterr().out
    assert code == 1
    assert "overall degraded" in out
