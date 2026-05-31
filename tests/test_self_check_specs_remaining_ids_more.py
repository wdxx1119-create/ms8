from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

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
            "settings": {"memory": {"self_check": {}, "security": {"shadow": {}}}},
        }
    )


def test_l1_check_coverage_warn_and_fail(monkeypatch, tmp_path: Path) -> None:
    core = _core(tmp_path)
    core.config["settings"]["memory"]["self_check"]["min_registered_checks"] = 10

    monkeypatch.setattr(cs, "build_check_specs", lambda level="FULL_PLUS": [object() for _ in range(11)])
    out_warn = cs._check_l1_check_coverage(core, {})
    assert out_warn["status"] == cs.STATUS_WARN

    monkeypatch.setattr(cs, "build_check_specs", lambda level="FULL_PLUS": [object() for _ in range(8)])
    out_fail = cs._check_l1_check_coverage(core, {})
    assert out_fail["status"] == cs.STATUS_FAIL


def test_l1_self_check_integrity_init_then_mismatch(tmp_path: Path) -> None:
    core = _core(tmp_path)
    out_init = cs._check_l1_self_check_integrity(core, {})
    assert out_init["status"] == cs.STATUS_WARN

    baseline = Path(core.config["memory_dir"]) / "reports" / "self_check_integrity_baseline.json"
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["hashes"]["check_specs.py"] = "mismatch"
    baseline.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out_fail = cs._check_l1_self_check_integrity(core, {})
    assert out_fail["status"] == cs.STATUS_FAIL


def test_l2_repair_audit_health_missing_and_malformed(tmp_path: Path) -> None:
    core = _core(tmp_path)
    out_missing = cs._check_l2_repair_audit_health(core, {})
    assert out_missing["status"] == cs.STATUS_WARN

    p = Path(core.config["memory_dir"]) / "logs" / "repair_ops_audit.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"ok":1}\n{bad}\n', encoding="utf-8")
    out_malformed = cs._check_l2_repair_audit_health(core, {})
    assert out_malformed["status"] == cs.STATUS_WARN


def test_l2_repair_lock_health_active_and_stale(tmp_path: Path) -> None:
    core = _core(tmp_path)
    out_no_lock = cs._check_l2_repair_lock_health(core, {})
    assert out_no_lock["status"] == cs.STATUS_PASS

    lock = Path(core.config["memory_dir"]) / "state" / "repair_in_progress.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("x", encoding="utf-8")
    stale = time.time() - 7200
    lock.touch()
    # set stale mtime
    import os

    os.utime(lock, (stale, stale))
    out_stale = cs._check_l2_repair_lock_health(core, {})
    assert out_stale["status"] == cs.STATUS_WARN


def test_l3_shadow_permissions_branches(monkeypatch, tmp_path: Path) -> None:
    core = _core(tmp_path)
    mod = ModuleType("shadow_permissions")

    def _fn(_shadow_dir: Path, backup_dir: Path):
        _ = backup_dir
        return {"violations": ["a"], "corrected": ["a"]}

    mod.ensure_shadow_permissions = _fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ms8.engine_core.security.shadow.shadow_permissions", mod)
    out_warn = cs._check_l3_shadow_permissions(core, {})
    assert out_warn["status"] == cs.STATUS_WARN

    def _fn2(_shadow_dir: Path, backup_dir: Path):
        _ = backup_dir
        return {"violations": [], "corrected": []}

    mod.ensure_shadow_permissions = _fn2  # type: ignore[attr-defined]
    out_pass = cs._check_l3_shadow_permissions(core, {})
    assert out_pass["status"] == cs.STATUS_PASS


def test_l3_shadow_health_warn_fail_pass(tmp_path: Path) -> None:
    core = _core(tmp_path)
    core.shadow_health = lambda: "bad"  # type: ignore[attr-defined]
    out_invalid = cs._check_l3_shadow_health(core, {})
    assert out_invalid["status"] == cs.STATUS_WARN

    core.shadow_health = lambda: {"ok": False, "issues": ["x"]}  # type: ignore[attr-defined]
    out_fail = cs._check_l3_shadow_health(core, {})
    assert out_fail["status"] == cs.STATUS_FAIL

    core.shadow_health = lambda: {"ok": True, "issues": []}  # type: ignore[attr-defined]
    out_pass = cs._check_l3_shadow_health(core, {})
    assert out_pass["status"] == cs.STATUS_PASS


def test_l3_repair_effectiveness_warn_and_fail(tmp_path: Path) -> None:
    core = _core(tmp_path)
    reports = Path(core.config["memory_dir"]) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    latest = reports / "self_check_latest.json"

    latest.write_text(
        json.dumps({"repair_summary": {"window_7d": {"total": 10, "success_rate": 0.6, "rollback_rate": 0.1}}}),
        encoding="utf-8",
    )
    out_warn = cs._check_l3_repair_effectiveness(core, {})
    assert out_warn["status"] == cs.STATUS_WARN

    latest.write_text(
        json.dumps({"repair_summary": {"window_7d": {"total": 10, "success_rate": 0.1, "rollback_rate": 0.1}}}),
        encoding="utf-8",
    )
    out_fail = cs._check_l3_repair_effectiveness(core, {})
    assert out_fail["status"] == cs.STATUS_FAIL


def test_m9_write_gateway_single_entry_executes(tmp_path: Path) -> None:
    core = _core(tmp_path)
    out = cs._check_m9_write_gateway_single_entry(core, {})
    assert out["status"] in {cs.STATUS_PASS, cs.STATUS_FAIL}
