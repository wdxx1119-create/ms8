from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ms8.engine_core.maintenance.self_repair import repair_orchestrator as ro
from ms8.engine_core.maintenance.self_repair.repair_schema import RepairPlanItem


@dataclass
class _Core:
    config: dict[str, str]


@dataclass
class _Policy:
    action: str
    domain: str
    risk: str
    depends_on: list[str]
    target: str


def test_op_id_shape() -> None:
    out = ro._op_id("c1", "repair_x")
    assert out.startswith("repair-")
    assert len(out.split("-")) >= 3


def test_topo_sort_basic_and_cycle_fallback() -> None:
    a = RepairPlanItem("1", "c1", "A", "memory", "R1", "r", "t")
    b = RepairPlanItem("2", "c2", "B", "security", "R1", "r", "t", depends_on=["A"])
    c = RepairPlanItem("3", "c3", "C", "connect", "R2", "r", "t", depends_on=["B"])

    ordered = ro._topo_sort([c, b, a])
    assert [x.action for x in ordered] == ["A", "B", "C"]

    # cycle: A depends_on B and B depends_on A -> deterministic fallback sort
    a2 = RepairPlanItem("1", "c1", "A", "memory", "R2", "r", "t", depends_on=["B"])
    b2 = RepairPlanItem("2", "c2", "B", "security", "R1", "r", "t", depends_on=["A"])
    cycle = ro._topo_sort([a2, b2])
    assert len(cycle) == 2
    assert set(x.action for x in cycle) == {"A", "B"}


def test_build_repair_plan_missing_and_error_report(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    core = _Core(config={"memory_dir": str(memory_dir)})

    out_missing = ro.build_repair_plan(core)
    assert out_missing["source_report_status"] == "missing"
    assert out_missing["plan_count"] == 0

    p = memory_dir / "reports" / "self_check_latest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{bad-json", encoding="utf-8")
    out_error = ro.build_repair_plan(core)
    assert out_error["source_report_status"] == "error"
    assert out_error["plan_count"] == 0


def test_build_repair_plan_filters_dedupe_and_params(tmp_path: Path, monkeypatch) -> None:
    memory_dir = tmp_path / "memory"
    reports = memory_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "self_check_latest.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "requested_level": "L4",
                "results": [
                    {"status": "warn", "check_id": "l2_jsonl_parse", "message": "bad", "details": {"bad_files": {"a.jsonl": 1}}},
                    {"status": "fail", "check_id": "l1_core_files", "message": "missing", "details": {"missing": ["x.py"]}},
                    {"status": "warn", "check_id": "l2_jsonl_parse", "message": "dup-should-dedupe"},
                    {"status": "pass", "check_id": "ignored"},
                    {"status": "error", "check_id": "unknown_policy"},
                ],
            }
        ),
        encoding="utf-8",
    )

    policies = {
        "l2_jsonl_parse": _Policy("repair_jsonl", "memory", "R1", [], "memory"),
        "l1_core_files": _Policy("restore_core_files", "security", "R2", ["repair_jsonl"], "core"),
    }
    monkeypatch.setattr(ro, "get_policy", lambda cid: policies.get(cid))

    core = _Core(config={"memory_dir": str(memory_dir)})
    out = ro.build_repair_plan(core, only_risk="R1")
    assert out["plan_count"] == 1
    assert out["plan"][0]["check_id"] == "l2_jsonl_parse"
    assert out["plan"][0]["params"]["target_file"] == "a.jsonl"

    out2 = ro.build_repair_plan(core, domain="security")
    assert out2["plan_count"] == 1
    assert out2["plan"][0]["check_id"] == "l1_core_files"
    assert out2["plan"][0]["params"]["missing_files"] == ["x.py"]

    out3 = ro.build_repair_plan(core)
    # dedupe by (action, target): l2_jsonl_parse appears once
    assert out3["plan_count"] == 2
    actions = [x["action"] for x in out3["plan"]]
    assert actions[0] == "repair_jsonl"
