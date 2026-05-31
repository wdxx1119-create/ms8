from __future__ import annotations

from ms8.app.pipeline.memory_admission_engine import evaluate_candidate
from ms8.engine_core.core import MemoryCore


def test_pipeline_admission_policy_override(monkeypatch) -> None:
    class _Engine:
        def evaluate_admission(self, payload):
            return {
                "ok": True,
                "code": "OK",
                "reason": "t",
                "trace_id": "p4",
                "data": {
                    "route": "pending_review",
                    "reasons": ["policy_override"],
                    "should_persist_main": True,
                    "should_index": False,
                    "should_write_memory_md": False,
                },
            }

    monkeypatch.setattr("ms8.app.pipeline.memory_admission_engine.get_policy_engine", lambda: _Engine())
    decision = evaluate_candidate("普通文本", metadata={"source": "t"})
    assert decision.route == "pending_review"
    assert decision.reasons == ["policy_override"]
    assert decision.should_index is False


def test_core_evaluate_admission_policy_override() -> None:
    class _Engine:
        def evaluate_admission(self, payload):
            return {
                "ok": True,
                "code": "OK",
                "reason": "t",
                "trace_id": "p42",
                "data": {
                    "route": "rejected",
                    "reasons": ["closed_policy_reject"],
                    "should_persist_main": False,
                    "should_index": False,
                    "should_write_memory_md": False,
                    "normalized_text": "x",
                },
            }

    core = object.__new__(MemoryCore)
    core.policy_engine = _Engine()
    out = core._evaluate_admission("abc", source="unit")
    assert out["route"] == "rejected"
    assert out["reasons"] == ["closed_policy_reject"]
    assert out["should_write_memory_md"] is False

