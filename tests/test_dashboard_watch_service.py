from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ms8 import dashboard, service, service_platform, watch


def test_dashboard_runs_and_prints_key_sections(monkeypatch, capsys, tmp_path: Path) -> None:
    root = tmp_path / "ms8_home"
    backups = root / "backups"
    logs = root / "logs"
    backups.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    (backups / "b1.json").write_text("{}", encoding="utf-8")
    (logs / "l1.log").write_text("ok", encoding="utf-8")

    monkeypatch.setattr(
        dashboard,
        "ensure_runtime_dirs",
        lambda: {
            "root": root,
            "backups": backups,
            "logs": logs,
            "memories": root / "data" / "memories.jsonl",
        },
    )
    monkeypatch.setattr(dashboard, "read_memories", lambda: [{"source": "u", "text": "hello"}])
    monkeypatch.setattr(dashboard, "count_memories", lambda: 1)
    monkeypatch.setattr(dashboard, "last_write_time", lambda: "2026-05-18T00:00:00Z")
    monkeypatch.setattr(dashboard, "engine_status", lambda: {"mode": "ms8_core", "available": True, "records_file": "x"})
    monkeypatch.setattr(dashboard, "get_engine_knowledge_graph_stats", lambda: {"entity_total": 1, "relation_total": 2})
    monkeypatch.setattr(dashboard, "get_engine_shadow_status", lambda: {"status": "ok", "sealed": False})
    monkeypatch.setattr(
        dashboard,
        "get_engine_llm_status",
        lambda: {
            "available": True,
            "providers": {"openai": {"enabled": True, "has_api_key": True, "client_ready": True}},
        },
    )
    monkeypatch.setattr(dashboard, "get_engine_monitoring_status", lambda: {"enabled": True, "alerts": [], "compression_freshness": {}})
    monkeypatch.setattr(
        dashboard,
        "absorb_health_summary",
        lambda: {
            "risk": "green",
            "authorized_roots": 1,
            "pending_review": 0,
            "quarantine": 0,
            "auto_submit_summaries": False,
            "auto_write_tier": "OFF",
            "kg_extract": {"pending_candidates": 0, "applied_total": 0},
        },
    )
    monkeypatch.setattr(dashboard, "get_expression_router_status", lambda: {"mode_counts": {}, "total_samples": 0, "strong_ratio": 0.0})
    monkeypatch.setattr(dashboard, "get_capability_reachability_report", lambda top_unreachable=10: {"reachable_ratio": 1.0, "referenced_methods": 1, "public_methods_total": 1, "unreachable_methods": 0})
    monkeypatch.setattr(dashboard, "run_engine_self_check", lambda level="L4": {"status": "ok", "results": []})
    monkeypatch.setattr(
        dashboard,
        "get_governance_report",
        lambda: {
            "noncanonical_records": 0,
            "schema_invalid_count": 0,
            "fallback_write_count": 0,
            "fallback_total_count": 0,
            "duplicate_groups": 0,
            "pending_review": 0,
            "pending_review_oldest_hours": 0,
            "revoked": 0,
            "superseded": 0,
            "trend": {"window_24h": {"risk": "green", "samples": 1, "delta": {}}, "window_7d": {"risk": "green", "samples": 1, "delta": {}}},
        },
    )

    rc = dashboard.run_dashboard(limit=1)
    out = capsys.readouterr().out
    assert rc == 0
    assert "MS8 Dashboard" in out
    assert "Engine:" in out
    assert "absorb: risk=green roots=1 pending=0 quarantine=0 autosubmit=False tier=OFF kg_pending=0 kg_applied=0" in out
    assert "Sources:" in out


def test_watch_once_recent_activity_skips_backup(monkeypatch, capsys) -> None:
    monkeypatch.setattr(watch, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(watch, "run_doctor", lambda: 0)
    monkeypatch.setattr(watch, "count_memories", lambda: 10)
    monkeypatch.setattr(watch, "run_daily_learning", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_kg_batch_extract", lambda limit=20, force=False: {"ran": True})
    monkeypatch.setattr(watch, "run_memory_tiering", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_graph_maintenance", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_reflection", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_synthetic_auto_confirm", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_engine_self_check", lambda level="L2": {"status": "ok"})
    monkeypatch.setattr(watch, "absorb_health_summary", lambda: {"risk": "green", "pending_review": 1, "quarantine": 0})
    monkeypatch.setattr(watch, "run_maintenance_now", lambda force=True: {"ok": True, "ran": True})
    monkeypatch.setattr(watch, "run_maintenance_policy", lambda: {"ran": False})
    monkeypatch.setattr(watch, "repair_compression_if_stale", lambda: {"ran": False})
    monkeypatch.setattr(watch, "repair_duplicates_after_compression", lambda: {"ok": True, "result": {"status": "skipped"}})
    monkeypatch.setattr(watch, "has_recent_activity", lambda window_seconds=300: True)
    monkeypatch.setattr(watch, "backup_memories", lambda tag="watch": {"path": "/tmp/not-used"})
    monkeypatch.setattr(watch, "cleanup_old_backups", lambda max_keep=20: {"removed_count": 0})

    rc = watch.run_watch(interval_seconds=10, once=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "backup=skipped" in out
    assert "absorb_risk=green absorb_pending=1 absorb_quarantine=0" in out


def test_watch_once_no_recent_activity_runs_backup(monkeypatch, capsys) -> None:
    monkeypatch.setattr(watch, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(watch, "run_doctor", lambda: 0)
    monkeypatch.setattr(watch, "count_memories", lambda: 10)
    monkeypatch.setattr(watch, "run_daily_learning", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_kg_batch_extract", lambda limit=20, force=False: {"ran": False})
    monkeypatch.setattr(watch, "run_memory_tiering", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_graph_maintenance", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_reflection", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_synthetic_auto_confirm", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_engine_self_check", lambda level="L2": {"status": "ok"})
    monkeypatch.setattr(watch, "absorb_health_summary", lambda: {"risk": "green", "pending_review": 0, "quarantine": 0})
    monkeypatch.setattr(watch, "run_maintenance_now", lambda force=True: {"ok": True, "ran": True})
    monkeypatch.setattr(watch, "run_maintenance_policy", lambda: {"ran": False})
    monkeypatch.setattr(watch, "repair_compression_if_stale", lambda: {"ran": True})
    monkeypatch.setattr(watch, "repair_duplicates_after_compression", lambda: {"ok": True, "result": {"status": "ok"}})
    monkeypatch.setattr(watch, "has_recent_activity", lambda window_seconds=300: False)
    monkeypatch.setattr(watch, "backup_memories", lambda tag="watch": {"path": "/tmp/b.json"})
    monkeypatch.setattr(watch, "cleanup_old_backups", lambda max_keep=20: {"removed_count": 2})

    rc = watch.run_watch(interval_seconds=10, once=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "backup=/tmp/b.json" in out
    assert "cleanup_removed=2" in out


def test_watch_fallback_to_maintenance_policy_when_now_fails(monkeypatch, capsys) -> None:
    monkeypatch.setattr(watch, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(watch, "run_doctor", lambda: 0)
    monkeypatch.setattr(watch, "count_memories", lambda: 10)
    monkeypatch.setattr(watch, "run_daily_learning", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_kg_batch_extract", lambda limit=20, force=False: {"ran": True})
    monkeypatch.setattr(watch, "run_memory_tiering", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_graph_maintenance", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_reflection", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_synthetic_auto_confirm", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_engine_self_check", lambda level="L2": {"status": "ok"})
    monkeypatch.setattr(watch, "absorb_health_summary", lambda: {"risk": "green", "pending_review": 0, "quarantine": 0})
    monkeypatch.setattr(watch, "run_maintenance_now", lambda force=True: {"ok": False, "ran": False})
    monkeypatch.setattr(watch, "run_maintenance_policy", lambda: {"ran": True, "source": "policy"})
    monkeypatch.setattr(watch, "repair_compression_if_stale", lambda: {"ran": False})
    monkeypatch.setattr(watch, "repair_duplicates_after_compression", lambda: {"ok": True, "result": {"status": "skipped"}})
    monkeypatch.setattr(watch, "has_recent_activity", lambda window_seconds=300: True)
    monkeypatch.setattr(watch, "backup_memories", lambda tag="watch": {"path": "/tmp/not-used"})
    monkeypatch.setattr(watch, "cleanup_old_backups", lambda max_keep=20: {"removed_count": 0})

    rc = watch.run_watch(interval_seconds=10, once=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "maintenance=True" in out


def test_watch_non_once_enforces_min_interval_and_sleeps(monkeypatch) -> None:
    monkeypatch.setattr(watch, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(watch, "run_doctor", lambda: 0)
    monkeypatch.setattr(watch, "count_memories", lambda: 1)
    monkeypatch.setattr(watch, "run_daily_learning", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_kg_batch_extract", lambda limit=20, force=False: {"ran": False})
    monkeypatch.setattr(watch, "run_memory_tiering", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_graph_maintenance", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_reflection", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_synthetic_auto_confirm", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_engine_self_check", lambda level="L2": {"status": "ok"})
    monkeypatch.setattr(watch, "absorb_health_summary", lambda: {"risk": "green", "pending_review": 0, "quarantine": 0})
    monkeypatch.setattr(watch, "run_maintenance_now", lambda force=True: {"ok": True, "ran": True})
    monkeypatch.setattr(watch, "run_maintenance_policy", lambda: {"ran": False})
    monkeypatch.setattr(watch, "repair_compression_if_stale", lambda: {"ran": False})
    monkeypatch.setattr(watch, "repair_duplicates_after_compression", lambda: {"ok": True, "result": {"status": "skipped"}})
    monkeypatch.setattr(watch, "has_recent_activity", lambda window_seconds=300: True)
    monkeypatch.setattr(watch, "backup_memories", lambda tag="watch": {"path": "/tmp/not-used"})
    monkeypatch.setattr(watch, "cleanup_old_backups", lambda max_keep=20: {"removed_count": 0})

    sleep_calls: list[int] = []

    def _stop_after_one_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        raise RuntimeError("stop-loop")

    monkeypatch.setattr(watch.time, "sleep", _stop_after_one_sleep)

    try:
        watch.run_watch(interval_seconds=1, once=False)
    except RuntimeError as exc:
        assert str(exc) == "stop-loop"

    # interval_seconds < 10 should be clamped to 10
    assert sleep_calls == [10]


def test_service_install_status_remove(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class _Backend:
        def install_watch(self, interval_seconds: int = 1800):
            calls.append(("install_watch", interval_seconds))
            return {"ok": True, "backend": "fake"}

        def watch_status(self):
            calls.append(("watch_status", None))
            return {"ok": True, "installed": True, "running": True, "backend": "fake"}

        def remove_watch(self):
            calls.append(("remove_watch", None))
            return {"ok": True, "backend": "fake"}

    monkeypatch.setattr(service, "current_service_backend", lambda: _Backend())

    installed = service.install_service(interval_seconds=60)
    status = service.service_status()
    removed = service.remove_service()

    assert installed["ok"] is True
    assert status["installed"] is True
    assert status["running"] is True
    assert removed["ok"] is True
    assert calls == [("install_watch", 60), ("watch_status", None), ("remove_watch", None)]


def test_absorb_service_install_status_remove(monkeypatch) -> None:
    calls: list[str] = []

    class _Backend:
        def install_absorb(self):
            calls.append("install_absorb")
            return {"ok": True, "backend": "fake"}

        def absorb_status(self):
            calls.append("absorb_status")
            return {"ok": True, "installed": True, "running": True, "backend": "fake"}

        def remove_absorb(self):
            calls.append("remove_absorb")
            return {"ok": True, "backend": "fake"}

    monkeypatch.setattr(service, "current_service_backend", lambda: _Backend())

    installed = service.install_absorb_service()
    status = service.absorb_service_status()
    removed = service.remove_absorb_service()

    assert installed["ok"] is True
    assert status["installed"] is True
    assert status["running"] is True
    assert removed["ok"] is True
    assert calls == ["install_absorb", "absorb_status", "remove_absorb"]


def test_install_all_project_memory_services_counts_only_successes(monkeypatch) -> None:
    monkeypatch.setattr(service, "list_projects", lambda: [{"name": "win-demo"}, {"name": "win-mainline"}])
    results = {
        "win-demo": {"ok": False, "project": "win-demo", "reason_code": "windows_service_install_failed"},
        "win-mainline": {"ok": True, "project": "win-mainline"},
    }
    monkeypatch.setattr(
        service,
        "install_project_memory_service",
        lambda name, auto_build=True, submit_summary=True, auto_index=True: results[name],
    )

    payload = service.install_all_project_memory_services()

    assert payload["ok"] is False
    assert payload["registered_projects"] == 2
    assert payload["services_installed"] == 1
    assert payload["services_failed"] == 1
    assert payload["results"][0]["project"] == "win-demo"
    assert payload["results"][1]["project"] == "win-mainline"


def test_project_memory_services_status_all_includes_runtime_mode_summary(monkeypatch) -> None:
    monkeypatch.setattr(service, "list_projects", lambda: [{"name": "win-demo"}, {"name": "win-mainline"}])
    monkeypatch.setattr("ms8.absorb.project_memory.health._watch_support", lambda: {"installed": True, "backend": "watchdog"})

    def _fake_status(name: str) -> dict:
        if name == "win-demo":
            return {
                "ok": True,
                "project": name,
                "backend": "schtasks",
                "installed": False,
                "running": False,
                "error_kind": "permission_denied",
            }
        return {
            "ok": True,
            "project": name,
            "backend": "launchd",
            "installed": True,
            "running": True,
        }

    monkeypatch.setattr(service, "project_memory_service_status", _fake_status)

    payload = service.project_memory_services_status_all()

    assert payload["ok"] is True
    assert payload["registered_projects"] == 2
    assert payload["installed_services"] == 1
    assert payload["running_services"] == 1
    assert payload["background_service_ready_projects"] == 1
    assert payload["foreground_watch_available_projects"] == 2
    assert payload["recommended_runtime_modes"] == {
        "background_service": 1,
        "foreground_watch": 1,
    }
    assert payload["results"][0]["recommended_runtime_mode"] == "foreground_watch"
    assert payload["results"][0]["runtime_hint"] == "Background scheduler is blocked by Windows permissions; use foreground watch."
    assert payload["results"][1]["recommended_runtime_mode"] == "background_service"


def test_service_remove_when_backend_reports_missing(monkeypatch) -> None:
    class _Backend:
        def remove_watch(self):
            return {"ok": True, "backend": "fake", "reason_code": "already_missing"}

    monkeypatch.setattr(service, "current_service_backend", lambda: _Backend())

    removed = service.remove_service()
    assert removed["ok"] is True
    assert removed["reason_code"] == "already_missing"


def test_program_arguments_uses_current_python_module(monkeypatch) -> None:
    monkeypatch.setattr(service_platform.sys, "executable", "/usr/local/bin/python3")
    args = service._program_arguments("watch", "--interval", "60")
    assert args == ["/usr/local/bin/python3", "-m", "ms8", "watch", "--interval", "60"]


def test_windows_scheduler_error_classifier_detects_permission_denied() -> None:
    payload = service_platform._classify_windows_scheduler_error("错误: 拒绝访问。", "")

    assert payload["error_kind"] == "permission_denied"
    assert payload["permission_required"] is True
    assert payload["scheduler_available"] is True


def test_windows_service_hint_uses_standard_reason_code() -> None:
    payload = service._with_windows_service_hint(
        {
            "ok": False,
            "backend": "schtasks",
            "project": "win-mainline",
            "error_kind": "permission_denied",
            "stderr": "错误: 拒绝访问。",
        },
        action="install",
        project="win-mainline",
    )

    assert payload["reason_code"] == "windows_service_permission_denied"
    assert payload["fallback_mode"] == "foreground_watch"
    assert payload["next_actions"][0] == "ms8 absorb project-memory watch --name win-mainline"
