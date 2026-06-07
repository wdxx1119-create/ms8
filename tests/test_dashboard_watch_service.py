from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ms8 import dashboard, service, watch


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


def test_service_install_status_remove(monkeypatch, tmp_path: Path) -> None:
    plist = tmp_path / "LaunchAgents" / "com.ms8.watch.plist"
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)

    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], capture_output: bool = True, text: bool = True):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=f"{service.LABEL}\n", stderr="")

    monkeypatch.setattr(service, "_plist_path", lambda: plist)
    monkeypatch.setattr(service, "get_runtime_dir", lambda: runtime)
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    monkeypatch.setattr(service.subprocess, "run", _fake_run)

    installed = service.install_service(interval_seconds=60)
    assert installed["ok"] is True
    assert plist.exists()

    status = service.service_status()
    assert status["ok"] is True
    assert status["installed"] is True
    assert status["running"] is True

    removed = service.remove_service()
    assert removed["ok"] is True
    assert not plist.exists()
    assert any("load" in c for c in calls)


def test_absorb_service_install_status_remove(monkeypatch, tmp_path: Path) -> None:
    plist = tmp_path / "LaunchAgents" / "com.ms8.absorb.watch.plist"
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)

    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], capture_output: bool = True, text: bool = True):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=f"{service.ABSORB_LABEL}\n", stderr="")

    monkeypatch.setattr(service, "_absorb_plist_path", lambda: plist)
    monkeypatch.setattr(service, "get_runtime_dir", lambda: runtime)
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    monkeypatch.setattr(service.subprocess, "run", _fake_run)

    installed = service.install_absorb_service()
    assert installed["ok"] is True
    assert plist.exists()
    assert "absorb" in plist.read_text(encoding="utf-8")

    status = service.absorb_service_status()
    assert status["ok"] is True
    assert status["installed"] is True
    assert status["running"] is True

    removed = service.remove_absorb_service()
    assert removed["ok"] is True
    assert not plist.exists()
    assert any("load" in c for c in calls)


def test_service_remove_when_plist_missing(monkeypatch, tmp_path: Path) -> None:
    plist = tmp_path / "LaunchAgents" / "com.ms8.watch.plist"
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], capture_output: bool = True, text: bool = True):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(service, "_plist_path", lambda: plist)
    monkeypatch.setattr(service.subprocess, "run", _fake_run)

    removed = service.remove_service()
    assert removed["ok"] is True


def test_program_arguments_prefers_ms8_binary(monkeypatch) -> None:
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/local/bin/ms8" if name == "ms8" else None)
    args = service._program_arguments("watch", "--interval", "60")
    assert args == ["/usr/local/bin/ms8", "watch", "--interval", "60"]
