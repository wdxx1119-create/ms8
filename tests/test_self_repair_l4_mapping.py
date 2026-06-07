from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_repair.repair_orchestrator import build_repair_plan


class _FakeCore:
    def __init__(self, memory_dir: Path) -> None:
        self.config = {"memory_dir": str(memory_dir)}


def _write_l4_report(memory_dir: Path, check_id: str, status: str = "fail") -> None:
    reports = memory_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    payload = {
        "requested_level": "L4",
        "status": "failed",
        "summary": {"total": 4, "pass": 3, "warn": 0, "fail": 1, "error": 0, "exit_code": 2},
        "results": [
            {"check_id": check_id, "status": status, "message": "mock l4 fail", "details": {}},
        ],
    }
    (reports / "self_check_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_l2_report(memory_dir: Path, check_id: str, status: str = "warn") -> None:
    reports = memory_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    payload = {
        "requested_level": "L2",
        "status": "failed",
        "summary": {"total": 1, "pass": 0, "warn": 1, "fail": 0, "error": 0, "exit_code": 1},
        "results": [
            {"check_id": check_id, "status": status, "message": "mock l2 warn", "details": {}},
        ],
    }
    (reports / "self_check_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_l4_capture_trend_has_repair_policy(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    _write_l4_report(memory_dir, "l4_capture_trend")
    core = _FakeCore(memory_dir)
    out = build_repair_plan(core, mode="dry-run")
    assert out["status"] == "ok"
    assert out["source_report_level"] == "L4"
    assert out["plan_count"] >= 1
    actions = [row.get("action") for row in out["plan"]]
    assert "run_self_check_l1" in actions


def test_l4_capacity_projection_maps_to_cleanup_disk(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    _write_l4_report(memory_dir, "l4_capacity_projection")
    core = _FakeCore(memory_dir)
    out = build_repair_plan(core, mode="dry-run")
    assert out["status"] == "ok"
    assert out["plan_count"] >= 1
    actions = [row.get("action") for row in out["plan"]]
    assert "cleanup_disk" in actions


def test_l4_absorb_health_maps_to_inspection(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    _write_l4_report(memory_dir, "l4_absorb_health", status="warn")
    core = _FakeCore(memory_dir)
    out = build_repair_plan(core, mode="dry-run")
    assert out["status"] == "ok"
    assert out["plan_count"] >= 1
    actions = [row.get("action") for row in out["plan"]]
    assert "inspect_absorb_health" in actions


def test_c15_agent_native_template_maps_to_dry_regen_hint(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    _write_l2_report(memory_dir, "c15_agent_native_template_semantics", status="warn")
    core = _FakeCore(memory_dir)
    out = build_repair_plan(core, mode="dry-run")
    assert out["status"] == "ok"
    assert out["plan_count"] >= 1
    actions = [row.get("action") for row in out["plan"]]
    assert "regenerate_agent_native_templates" in actions
