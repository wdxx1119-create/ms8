from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


def test_to_aware_and_result_helpers() -> None:
    aware = cs._to_aware("2026-05-22T10:00:00+08:00")
    assert aware is not None and aware.tzinfo is not None
    assert cs._to_aware("not-a-date") is None
    assert cs._ok("ok", {"a": 1})["status"] == "pass"
    assert cs._warn("w", {"b": 2})["status"] == "warn"
    assert cs._fail("f", {"c": 3})["status"] == "fail"


def test_pipeline_log_candidates_and_loaders(tmp_path: Path) -> None:
    p1 = tmp_path / "auto_memory_pipeline.log"
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    p2 = logs / "auto_memory_pipeline.1.log"
    p3 = logs / "auto_memory_pipeline.2.log"
    p1.write_text("", encoding="utf-8")
    p2.write_text("", encoding="utf-8")
    p3.write_text("", encoding="utf-8")
    names = {p.name for p in cs._pipeline_log_candidates(tmp_path)}
    assert {"auto_memory_pipeline.log", "auto_memory_pipeline.1.log", "auto_memory_pipeline.2.log"} <= names

    y = tmp_path / "x.yaml"
    y.write_text("a: 1\nb: test\n", encoding="utf-8")
    assert cs._load_yaml(y)["a"] == 1
    j = tmp_path / "x.json"
    j.write_text(json.dumps({"k": "v"}), encoding="utf-8")
    assert cs._load_json(j)["k"] == "v"


def test_connect_root_and_package_root(tmp_path: Path) -> None:
    class _Core:
        config = {"settings": {"memory": {"connect": {"root": str(tmp_path / "connect_root")}}}}

    root = cs._connect_root(_Core())
    assert root == (tmp_path / "connect_root")
    pkg = cs._connect_package_root()
    assert pkg.exists()


def test_build_specs_and_levels() -> None:
    l1_specs = cs.build_check_specs("L1")
    assert l1_specs
    assert all(spec.level == "L1" for spec in l1_specs)

    full = cs.build_check_specs("FULL_PLUS")
    assert full
    names = {spec.check_id for spec in full}
    assert "l1_core_files" in names
    assert "l4_capture_trend" in names

    assert cs.levels_for_run("L1") == ["L1"]
    assert cs.levels_for_run("L2") == ["L2"]
    assert cs.levels_for_run("L3") == ["L3"]
    assert cs.levels_for_run("L4") == ["L4"]
    assert cs.levels_for_run("FULL") == ["L1", "L2", "L3"]
    assert cs.levels_for_run("L1L2L3") == ["L1", "L2", "L3"]
    assert cs.levels_for_run("L1L2L3L4") == ["L1", "L2", "L3", "L4"]
    assert cs.levels_for_run("FULL_PLUS") == ["L1", "L2", "L3", "L4"]
    assert cs.levels_for_run("unknown") == ["L1"]


def test_build_specs_level_aliases_and_fallback() -> None:
    l2 = cs.build_check_specs("L2")
    assert l2
    assert all(spec.level == "L2" for spec in l2)

    l3 = cs.build_check_specs("L3")
    assert l3
    assert all(spec.level == "L3" for spec in l3)

    l4 = cs.build_check_specs("L4")
    assert l4
    assert all(spec.level == "L4" for spec in l4)

    full = cs.build_check_specs("FULL")
    assert full
    assert all(spec.level in {"L1", "L2", "L3"} for spec in full)

    alias = cs.build_check_specs("L1L2L3")
    assert alias
    assert all(spec.level in {"L1", "L2", "L3"} for spec in alias)

    unknown = cs.build_check_specs("UNKNOWN_LEVEL")
    assert unknown
    assert all(spec.level == "L1" for spec in unknown)
