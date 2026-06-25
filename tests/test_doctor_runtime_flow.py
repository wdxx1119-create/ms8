from __future__ import annotations

import json
from pathlib import Path

from ms8 import doctor, runtime


def test_run_doctor_healthy_exit_and_layer_output(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setattr(doctor, "ensure_runtime_dirs", lambda: {"root": tmp_path / ".ms8"})
    monkeypatch.setattr(doctor, "count_memories", lambda: 3)
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
    monkeypatch.setattr(doctor, "get_engine_monitoring_status", lambda: {"enabled": True, "alerts": []})
    monkeypatch.setattr(
        doctor,
        "get_engine_shadow_status",
        lambda: {"enabled": True, "mode": "active", "sealed": False, "manifest": {"reason": ""}},
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
            "health_domains": {"memory_quality_health": "green"},
        },
    )
    monkeypatch.setattr(
        doctor,
        "get_policy_backend_status",
        lambda: {
            "policy_backend": "open",
            "policy_engine_version": "0.1.0-open",
            "policy_fallback_reason": "",
            "policy_module": "ms8_policy_core",
            "policy_strict_mode": False,
            "policy_license": {
                "status": "disabled",
                "reason_code": "license_check_disabled",
                "enabled": False,
            },
        },
    )
    monkeypatch.setattr(
        doctor,
        "absorb_health_summary",
        lambda: {
            "risk": "green",
            "authorized_roots": 1,
            "pending_review": 0,
            "quarantine": 0,
            "auto_submit_summaries": False,
            "auto_write_tier": "OFF",
        },
    )
    monkeypatch.setattr(doctor, "get_expression_router_status", lambda: {"total_samples": 0, "mode_counts": {}})
    monkeypatch.setattr(doctor, "get_capability_reachability_report", lambda top_unreachable=10: {"reachable_ratio": 1.0, "referenced_methods": 1, "public_methods_total": 1, "unreachable_methods": 0})
    monkeypatch.setattr(doctor, "_agent_native_status", lambda: {"policy": "PRESENT", "permission_profile": "DEFAULT_SAFE", "task_files": "install=P, ops=P, usage=P", "agent_native_status": "OK"})
    monkeypatch.setattr(doctor, "has_recent_activity", lambda _w=3600: True)

    code = doctor.run_doctor()
    out = capsys.readouterr().out
    assert code == 0
    assert "--- Health Layers ---" in out
    assert "runtime_health: healthy" in out
    assert "memory_quality_health: healthy" in out
    assert "security_governance_health: healthy" in out
    assert "Overall: healthy" in out
    assert "policy license: status=disabled enabled=False" in out
    assert "absorb: risk=green roots=1 pending=0 quarantine=0 autosubmit=False tier=OFF" in out
    assert "absorb next:" not in out


def test_run_doctor_absorb_warning_prints_shortest_action(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setattr(doctor, "ensure_runtime_dirs", lambda: {"root": tmp_path / ".ms8"})
    monkeypatch.setattr(doctor, "count_memories", lambda: 3)

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
    monkeypatch.setattr(doctor, "get_engine_monitoring_status", lambda: {"enabled": False, "alerts": []})
    monkeypatch.setattr(
        doctor,
        "get_engine_shadow_status",
        lambda: {"enabled": True, "mode": "active", "sealed": False, "manifest": {"reason": ""}},
    )
    monkeypatch.setattr(doctor, "get_engine_llm_status", lambda: {"available": False, "providers": {}})
    monkeypatch.setattr(doctor, "get_llm_status_runtime", lambda: {})
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
            "pending_review_oldest_hours": 0,
            "self_check_status": "ok",
            "trend": {"window_24h": {"samples": 0, "risk": "green"}, "window_7d": {"samples": 0, "risk": "green"}},
        },
    )
    monkeypatch.setattr(doctor, "get_expression_router_status", lambda: {"total_samples": 0, "mode_counts": {}})
    monkeypatch.setattr(doctor, "get_capability_reachability_report", lambda top_unreachable=10: {"reachable_ratio": 1.0, "referenced_methods": 1, "public_methods_total": 1, "unreachable_methods": 0})
    monkeypatch.setattr(doctor, "_agent_native_status", lambda: {"policy": "PRESENT", "permission_profile": "DEFAULT_SAFE", "task_files": "install=P, ops=P, usage=P", "agent_native_status": "OK"})
    monkeypatch.setattr(doctor, "has_recent_activity", lambda _w=3600: True)
    monkeypatch.setattr(
        doctor,
        "get_policy_backend_status",
        lambda: {
            "policy_backend": "open",
            "policy_engine_version": "0.1.0-open",
            "policy_strict_mode": False,
            "policy_fallback_reason": "",
            "policy_license": {"status": "disabled", "enabled": False},
        },
    )
    monkeypatch.setattr(
        doctor,
        "absorb_health_summary",
        lambda: {
            "risk": "yellow",
            "authorized_roots": 1,
            "pending_review": 2,
            "quarantine": 0,
            "auto_submit_summaries": False,
            "auto_write_tier": "OFF",
        },
    )

    assert doctor.run_doctor() == 0
    out = capsys.readouterr().out
    assert "absorb: risk=yellow" in out
    assert "absorb next: ms8 absorb review list" in out


def test_run_doctor_self_check_guidance_prints_known_next_action(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setattr(doctor, "ensure_runtime_dirs", lambda: {"root": tmp_path / ".ms8"})
    monkeypatch.setattr(doctor, "count_memories", lambda: 3)

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
            "status": "fail",
            "summary": {"total": 1, "pass": 0, "warn": 0, "fail": 1, "error": 0, "exit_code": 2},
            "results": [{"check_id": "m3_review_queue_sla", "status": "fail"}],
            "domain_summary": {"memory": {"total": 1, "pass": 0, "warn": 0, "fail": 1, "error": 0, "pass_rate": 0.0}},
            "maturity_gate": {"memory_ready": False, "security_ready": True, "connect_ready": True, "overall_ready": False},
        },
    )
    monkeypatch.setattr(doctor, "get_engine_monitoring_status", lambda: {"enabled": False, "alerts": []})
    monkeypatch.setattr(
        doctor,
        "get_engine_shadow_status",
        lambda: {"enabled": True, "mode": "active", "sealed": False, "manifest": {"reason": ""}},
    )
    monkeypatch.setattr(doctor, "get_engine_llm_status", lambda: {"available": False, "providers": {}})
    monkeypatch.setattr(doctor, "get_llm_status_runtime", lambda: {})
    monkeypatch.setattr(
        doctor,
        "get_governance_report",
        lambda: {
            "noncanonical_records": 0,
            "schema_invalid_count": 0,
            "fallback_write_count": 0,
            "fallback_total_count": 0,
            "duplicate_groups": 0,
            "pending_review": 3,
            "pending_review_oldest_hours": 52.0,
            "self_check_status": "fail",
            "trend": {"window_24h": {"samples": 0, "risk": "green"}, "window_7d": {"samples": 0, "risk": "green"}},
        },
    )
    monkeypatch.setattr(doctor, "get_expression_router_status", lambda: {"total_samples": 0, "mode_counts": {}})
    monkeypatch.setattr(doctor, "get_capability_reachability_report", lambda top_unreachable=10: {"reachable_ratio": 1.0, "referenced_methods": 1, "public_methods_total": 1, "unreachable_methods": 0})
    monkeypatch.setattr(doctor, "_agent_native_status", lambda: {"policy": "PRESENT", "permission_profile": "DEFAULT_SAFE", "task_files": "install=P, ops=P, usage=P", "agent_native_status": "OK"})
    monkeypatch.setattr(doctor, "has_recent_activity", lambda _w=3600: True)
    monkeypatch.setattr(
        doctor,
        "get_policy_backend_status",
        lambda: {
            "policy_backend": "open",
            "policy_engine_version": "0.1.0-open",
            "policy_strict_mode": False,
            "policy_fallback_reason": "",
            "policy_license": {"status": "disabled", "enabled": False},
        },
    )
    monkeypatch.setattr(
        doctor,
        "absorb_health_summary",
        lambda: {
            "risk": "green",
            "authorized_roots": 1,
            "pending_review": 0,
            "quarantine": 0,
            "auto_submit_summaries": False,
            "auto_write_tier": "OFF",
        },
    )

    code = doctor.run_doctor()
    out = capsys.readouterr().out

    assert code == 1
    assert "self-check fail checks: m3_review_queue_sla" in out
    assert "m3_review_queue_sla: review queue backlog age or pending volume exceeded SLA." in out
    assert "self-check next: ms8 review list" in out
    assert "Overall: degraded" in out
    assert "watch next: ms8 watch --once" in out
    assert "watch also: ms8 ops self-check-report" in out


def test_run_doctor_overall_warn_follows_governance_domain_and_prints_follow_up(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setattr(doctor, "ensure_runtime_dirs", lambda: {"root": tmp_path / ".ms8"})
    monkeypatch.setattr(doctor, "count_memories", lambda: 3)

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
            "status": "warn",
            "summary": {"total": 1, "pass": 0, "warn": 1, "fail": 0, "error": 0, "exit_code": 1},
            "results": [{"check_id": "l4_capture_trend", "status": "warn"}],
            "domain_summary": {"memory": {"total": 1, "pass": 0, "warn": 1, "fail": 0, "error": 0, "pass_rate": 0.0}},
            "maturity_gate": {"memory_ready": True, "security_ready": True, "connect_ready": True, "overall_ready": True},
        },
    )
    monkeypatch.setattr(doctor, "get_engine_monitoring_status", lambda: {"enabled": True, "alerts": []})
    monkeypatch.setattr(
        doctor,
        "get_engine_shadow_status",
        lambda: {"enabled": True, "mode": "active", "sealed": False, "manifest": {"reason": ""}},
    )
    monkeypatch.setattr(doctor, "get_engine_llm_status", lambda: {"available": True, "providers": {}})
    monkeypatch.setattr(doctor, "get_llm_status_runtime", lambda: {})
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
            "pending_review_oldest_hours": 0,
            "self_check_status": "warn",
            "trend": {"window_24h": {"samples": 1, "risk": "yellow"}, "window_7d": {"samples": 1, "risk": "green"}},
            "health_domains": {"memory_quality_health": "yellow", "overall": "yellow"},
        },
    )
    monkeypatch.setattr(doctor, "get_expression_router_status", lambda: {"total_samples": 0, "mode_counts": {}})
    monkeypatch.setattr(doctor, "get_capability_reachability_report", lambda top_unreachable=10: {"reachable_ratio": 1.0, "referenced_methods": 1, "public_methods_total": 1, "unreachable_methods": 0})
    monkeypatch.setattr(doctor, "_agent_native_status", lambda: {"policy": "PRESENT", "permission_profile": "DEFAULT_SAFE", "task_files": "install=P, ops=P, usage=P", "agent_native_status": "OK"})
    monkeypatch.setattr(doctor, "has_recent_activity", lambda _w=3600: True)
    monkeypatch.setattr(
        doctor,
        "get_policy_backend_status",
        lambda: {
            "policy_backend": "open",
            "policy_engine_version": "0.1.0-open",
            "policy_strict_mode": False,
            "policy_fallback_reason": "",
            "policy_license": {"status": "disabled", "enabled": False},
        },
    )
    monkeypatch.setattr(
        doctor,
        "absorb_health_summary",
        lambda: {
            "risk": "green",
            "authorized_roots": 1,
            "pending_review": 0,
            "quarantine": 0,
            "auto_submit_summaries": False,
            "auto_write_tier": "OFF",
        },
    )

    code = doctor.run_doctor()
    out = capsys.readouterr().out

    assert code == 1
    assert "memory_quality_health: warn" in out
    assert "Overall: warn" in out
    assert "watch next: ms8 ops governance" in out


def test_run_doctor_shadow_startup_integrity_failure_degrades_and_points_to_shadow_actions(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setattr(doctor, "ensure_runtime_dirs", lambda: {"root": tmp_path / ".ms8"})
    monkeypatch.setattr(doctor, "count_memories", lambda: 3)

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
            "summary": {"total": 1, "pass": 1, "warn": 0, "fail": 0, "error": 0, "exit_code": 0},
            "results": [{"check_id": "c1", "status": "pass"}],
            "domain_summary": {"security": {"total": 1, "pass": 1, "warn": 0, "fail": 0, "error": 0, "pass_rate": 1.0}},
            "maturity_gate": {"memory_ready": True, "security_ready": True, "connect_ready": True, "overall_ready": True},
        },
    )
    monkeypatch.setattr(doctor, "get_engine_monitoring_status", lambda: {"enabled": True, "alerts": []})
    monkeypatch.setattr(
        doctor,
        "get_engine_shadow_status",
        lambda: {
            "enabled": True,
            "mode": "sealed",
            "sealed": True,
            "seal_level": "hard",
            "manifest": {"reason": "startup_integrity_failed"},
        },
    )
    monkeypatch.setattr(doctor, "get_engine_llm_status", lambda: {"available": True, "providers": {}})
    monkeypatch.setattr(doctor, "get_llm_status_runtime", lambda: {})
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
            "pending_review_oldest_hours": 0,
            "self_check_status": "ok",
            "shadow_runtime": {
                "enabled": True,
                "available": True,
                "status": "degraded",
                "mode": "sealed",
                "sealed": True,
                "seal_level": "hard",
                "reason": "startup_integrity_failed",
                "startup_findings": ["manifest_signature_invalid"],
                "manifest_signature_valid": False,
            },
            "trend": {"window_24h": {"samples": 1, "risk": "green"}, "window_7d": {"samples": 1, "risk": "green"}},
            "health_domains": {
                "runtime_health": "red",
                "memory_quality_health": "green",
                "retrieval_safety_health": "green",
                "security_integrity_health": "red",
                "lifecycle_maintenance_health": "green",
                "overall": "red",
            },
        },
    )
    monkeypatch.setattr(doctor, "get_expression_router_status", lambda: {"total_samples": 0, "mode_counts": {}})
    monkeypatch.setattr(doctor, "get_capability_reachability_report", lambda top_unreachable=10: {"reachable_ratio": 1.0, "referenced_methods": 1, "public_methods_total": 1, "unreachable_methods": 0})
    monkeypatch.setattr(doctor, "_agent_native_status", lambda: {"policy": "PRESENT", "permission_profile": "DEFAULT_SAFE", "task_files": "install=P, ops=P, usage=P", "agent_native_status": "OK"})
    monkeypatch.setattr(doctor, "has_recent_activity", lambda _w=3600: True)
    monkeypatch.setattr(
        doctor,
        "get_policy_backend_status",
        lambda: {
            "policy_backend": "open",
            "policy_engine_version": "0.1.0-open",
            "policy_strict_mode": False,
            "policy_fallback_reason": "",
            "policy_license": {"status": "disabled", "enabled": False},
        },
    )
    monkeypatch.setattr(
        doctor,
        "absorb_health_summary",
        lambda: {
            "risk": "green",
            "authorized_roots": 1,
            "pending_review": 0,
            "quarantine": 0,
            "auto_submit_summaries": False,
            "auto_write_tier": "OFF",
        },
    )

    code = doctor.run_doctor()
    out = capsys.readouterr().out

    assert code == 1
    assert "shadow: mode=sealed sealed=True level=hard reason=startup_integrity_failed" in out
    assert "shadow startup findings: manifest_signature_invalid" in out
    assert "shadow next: ms8 shadow status" in out
    assert "Overall: degraded" in out


def test_run_doctor_with_hint_handles_permission_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(doctor, "run_doctor", lambda: (_ for _ in ()).throw(PermissionError("denied")))
    code = doctor.run_doctor_with_hint()
    out = capsys.readouterr().out
    assert code == 1
    assert "hint: check runtime permissions" in out


def test_ensure_runtime_dirs_creates_defaults(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ms8home"
    monkeypatch.setenv("MS8_HOME", str(root))

    class _DummyEngine:
        @staticmethod
        def records_file():
            return root / "data" / "memories.jsonl"

    monkeypatch.setattr(runtime, "_engine", lambda: _DummyEngine())
    out = runtime.ensure_runtime_dirs()
    assert out["root"].exists()
    assert (root / "memory" / "compression_state.json").exists()
    assert (root / "memory" / "logs" / "repair_ops_audit.jsonl").exists()
    cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
    assert "governance_risk" in cfg
