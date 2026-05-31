from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.maintenance.self_check import check_specs as cs


def _core(tmp_path: Path) -> SimpleNamespace:
    workspace = tmp_path / "ws"
    memory = tmp_path / "mem"
    workspace.mkdir(parents=True, exist_ok=True)
    memory.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        config={
            "workspace_dir": str(workspace),
            "memory_dir": str(memory),
            "settings": {
                "memory": {
                    "self_check": {
                        "canary_path": "canary_probe.tmp",
                        "health_card_compare_target": "latest",
                    },
                    "connect": {"root": str(tmp_path / "connect")},
                    "monitoring": {"alerts": {"self_check_stale_hours": 2}},
                }
            },
        },
        shadow_status=lambda: {"sealed": False, "mode": "active"},
    )


def test_l1_launchd_mcp_warn_when_not_running(monkeypatch, tmp_path: Path):
    core = _core(tmp_path)
    monkeypatch.setattr(cs, "_launchctl_running", lambda _label: False)
    result = cs._check_l1_launchd_mcp(core, {})
    assert result["status"] == cs.STATUS_WARN
    assert "not running" in result["message"]


def test_l1_launchd_mcp_pass_when_runtime_health_ok(monkeypatch, tmp_path: Path):
    core = _core(tmp_path)
    connect_root = Path(core.config["settings"]["memory"]["connect"]["root"])
    (connect_root / "runtime").mkdir(parents=True, exist_ok=True)
    (connect_root / "runtime" / "health.json").write_text(
        json.dumps({"mcp_server": {"ok": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cs, "_launchctl_running", lambda _label: False)
    out = cs._check_l1_launchd_mcp(core, {})
    assert out["status"] == cs.STATUS_PASS
    assert out["details"]["mode"] == "standalone"


def test_l1_launchd_maintenance_ok_when_recent_report(monkeypatch, tmp_path: Path):
    core = _core(tmp_path)
    reports = Path(core.config["memory_dir"]) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": cs._now_iso()}
    (reports / "self_check_latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(cs, "_launchctl_running", lambda _label: False)
    result = cs._check_l1_launchd_maintenance(core, {})
    assert result["status"] == cs.STATUS_PASS
    assert "recent self-check run" in result["message"]


def test_l1_launchd_maintenance_ok_standalone_allowed_when_no_report(monkeypatch, tmp_path: Path):
    core = _core(tmp_path)
    monkeypatch.setattr(cs, "_launchctl_running", lambda _label: False)
    out = cs._check_l1_launchd_maintenance(core, {})
    assert out["status"] == cs.STATUS_PASS
    assert out["details"]["mode"] == "standalone_allowed"


def test_l1_launchd_maintenance_handles_bad_latest_json(monkeypatch, tmp_path: Path):
    core = _core(tmp_path)
    reports = Path(core.config["memory_dir"]) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "self_check_latest.json").write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(cs, "_launchctl_running", lambda _label: False)
    out = cs._check_l1_launchd_maintenance(core, {})
    assert out["status"] == cs.STATUS_PASS
    assert out["details"]["mode"] == "standalone_allowed"


def test_l1_core_files_fail_when_missing(tmp_path: Path):
    core = _core(tmp_path)
    result = cs._check_l1_core_files(core, {})
    assert result["status"] == cs.STATUS_FAIL
    assert result["details"]["missing"]


def test_l1_core_files_pass_when_present(tmp_path: Path):
    core = _core(tmp_path)
    ws = Path(core.config["workspace_dir"])
    mem = Path(core.config["memory_dir"])
    (ws / "MEMORY.md").write_text("# memory", encoding="utf-8")
    (ws / "config.yaml").write_text("a: 1", encoding="utf-8")
    (mem / "memory.db").write_text("db", encoding="utf-8")
    (mem / "knowledge_graph.db").write_text("kg", encoding="utf-8")
    result = cs._check_l1_core_files(core, {})
    assert result["status"] == cs.STATUS_PASS


def test_l1_shadow_files_warn_when_missing(tmp_path: Path):
    core = _core(tmp_path)
    result = cs._check_l1_shadow_files(core, {})
    assert result["status"] == cs.STATUS_WARN


def test_l1_canary_io_pass(tmp_path: Path):
    core = _core(tmp_path)
    result = cs._check_l1_canary_io(core, {})
    assert result["status"] == cs.STATUS_PASS


def test_l1_disk_space_warn(monkeypatch, tmp_path: Path):
    core = _core(tmp_path)
    core.config["settings"]["memory"]["self_check"]["disk_warn_gb"] = 100.0
    core.config["settings"]["memory"]["self_check"]["disk_crit_gb"] = 50.0

    class _Usage:
        free = 10 * (1024**3)

    monkeypatch.setattr(cs.shutil, "disk_usage", lambda _p: _Usage())
    result = cs._check_l1_disk_space(core, {})
    assert result["status"] == cs.STATUS_FAIL


def test_l1_disk_space_warn_and_pass_branches(monkeypatch, tmp_path: Path):
    core = _core(tmp_path)
    core.config["settings"]["memory"]["self_check"]["disk_warn_gb"] = 20.0
    core.config["settings"]["memory"]["self_check"]["disk_crit_gb"] = 5.0

    class _WarnUsage:
        free = 10 * (1024**3)

    monkeypatch.setattr(cs.shutil, "disk_usage", lambda _p: _WarnUsage())
    out_warn = cs._check_l1_disk_space(core, {})
    assert out_warn["status"] == cs.STATUS_WARN

    class _PassUsage:
        free = 50 * (1024**3)

    monkeypatch.setattr(cs.shutil, "disk_usage", lambda _p: _PassUsage())
    out_pass = cs._check_l1_disk_space(core, {})
    assert out_pass["status"] == cs.STATUS_PASS


def test_l1_health_card_diff_warn_when_target_missing(tmp_path: Path):
    core = _core(tmp_path)
    result = cs._check_l1_health_card_diff(core, {})
    assert result["status"] == cs.STATUS_WARN
    assert "target missing" in result["message"]


def test_l1_shadow_sealed_warn(tmp_path: Path):
    core = _core(tmp_path)
    core.shadow_status = lambda: {"sealed": True, "mode": "seal"}
    result = cs._check_l1_shadow_sealed(core, {})
    assert result["status"] == cs.STATUS_WARN


def test_l1_self_check_framework_warn_when_no_evidence(tmp_path: Path):
    core = _core(tmp_path)
    core.config["settings"]["memory"]["self_check"]["heartbeat_path"] = str(tmp_path / "missing.hb")
    result = cs._check_l1_self_check_framework(core, {})
    assert result["status"] == cs.STATUS_WARN
