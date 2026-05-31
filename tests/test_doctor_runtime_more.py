from __future__ import annotations

from pathlib import Path

from ms8 import doctor


def _patch_doctor_baseline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setattr(doctor, "ensure_runtime_dirs", lambda: {"root": tmp_path / ".ms8"})
    monkeypatch.setattr(doctor, "count_memories", lambda: 1)

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
            "domain_summary": {"memory": {"total": 1, "pass": 1, "warn": 0, "fail": 0, "error": 0, "pass_rate": 1.0}},
            "maturity_gate": {"memory_ready": True, "security_ready": True, "connect_ready": True, "overall_ready": True},
        },
    )
    monkeypatch.setattr(doctor, "get_engine_monitoring_status", lambda: {"enabled": True, "alerts": []})
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


def test_run_doctor_allow_degraded_env_returns_zero(monkeypatch, tmp_path: Path) -> None:
    _patch_doctor_baseline(monkeypatch, tmp_path)
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
            "baseline_update_pending": True,
            "trend": {"window_24h": {"samples": 1, "risk": "green", "delta": {}}, "window_7d": {"samples": 1, "risk": "green", "delta": {}}},
            "health_domains": {"memory_quality_health": "green"},
        },
    )
    monkeypatch.setenv("MS8_DOCTOR_ALLOW_DEGRADED", "1")
    code = doctor.run_doctor()
    assert code == 0


def test_run_doctor_with_hint_handles_os_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(doctor, "run_doctor", lambda: (_ for _ in ()).throw(OSError("io-fail")))
    code = doctor.run_doctor_with_hint()
    out = capsys.readouterr().out
    assert code == 1
    assert "hint: try backup and cleanup" in out


def test_run_backup_and_cleanup_prints_summary(monkeypatch, capsys) -> None:
    monkeypatch.setattr(doctor, "backup_memories", lambda tag="manual": {"path": "/tmp/backup.json"})
    monkeypatch.setattr(doctor, "cleanup_old_backups", lambda max_keep=20: {"removed_count": 3})
    code = doctor.run_backup_and_cleanup(max_keep=10)
    out = capsys.readouterr().out
    assert code == 0
    assert "backup: /tmp/backup.json" in out
    assert "cleanup removed: 3" in out


def test_run_set_risk_thresholds_prints_payload(monkeypatch, capsys) -> None:
    monkeypatch.setattr(doctor, "update_governance_risk_config", lambda **kwargs: {"ok": True, **kwargs})
    code = doctor.run_set_risk_thresholds(red_schema_invalid_gt=2, yellow_pending_review_gt=5)
    out = capsys.readouterr().out
    assert code == 0
    assert "updated governance risk thresholds:" in out
    assert "red_schema_invalid_gt" in out

