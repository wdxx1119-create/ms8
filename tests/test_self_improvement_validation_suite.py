from __future__ import annotations

from pathlib import Path

from ms8.engine_core.self_improvement import SelfImprovementEngine


class _MemoryCore:
    def __init__(self, tmp_path: Path) -> None:
        self.config = {
            "memory_dir": tmp_path,
            "settings": {
                "memory": {},
            },
        }


def test_run_validation_suite_errors_when_no_tests(tmp_path: Path) -> None:
    engine = SelfImprovementEngine(_MemoryCore(tmp_path))

    out = engine.run_validation_suite()

    assert out["status"] == "error"
    assert out["ok"] is False
    assert out["total_tests"] == 0
    assert out["message"] == "validation suite contains no executable tests"


def test_run_validation_suite_marks_success_when_tests_pass(tmp_path: Path) -> None:
    engine = SelfImprovementEngine(_MemoryCore(tmp_path))
    engine.test_suite["memory_tests"].append({"name": "smoke", "input": {"query": "x"}, "expected": {"ok": True}})
    engine.test_suite["skill_tests"].append({"name": "skill-smoke", "input": {"skill": "demo"}, "expected": {"ok": True}})

    out = engine.run_validation_suite()

    assert out["status"] == "success"
    assert out["ok"] is True
    assert out["total_tests"] == 2
    assert out["passed"] == 2
    assert out["failed"] == 0
    assert out["message"] == "validation suite passed"


def test_run_validation_suite_fails_invalid_case_shape(tmp_path: Path) -> None:
    engine = SelfImprovementEngine(_MemoryCore(tmp_path))
    engine.test_suite["memory_tests"].append({"name": "broken", "input": {"query": "x"}})

    out = engine.run_validation_suite()

    assert out["status"] == "failed"
    assert out["ok"] is False
    assert out["total_tests"] == 1
    assert out["passed"] == 0
    assert out["failed"] == 1
    assert out["details"][0]["reason"] == "missing_expected"
