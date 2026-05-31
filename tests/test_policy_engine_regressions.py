from __future__ import annotations

import time
from typing import Any

import pytest

from ms8.app.pipeline import memory_admission_engine as mae
from ms8.engine_core.policy_engine_open import OpenPolicyEngine


class _PolicyAcceptStub:
    def evaluate_admission(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "code": "OK",
            "reason": "stub",
            "trace_id": "t1",
            "data": {
                "route": "accepted",
                "normalized_text": str(payload.get("text", "")),
                "reasons": ["policy_accept"],
                "should_persist_main": True,
                "should_index": True,
                "should_write_memory_md": True,
                "redacted": False,
                "replace_old": False,
            },
        }


def test_admission_policy_first_no_dual_track_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mae, "get_policy_engine", lambda: _PolicyAcceptStub())
    # Local rules would normally classify this as short_term_only/noise.
    decision = mae.evaluate_candidate("ok")
    assert decision.route == "accepted"
    assert decision.reasons == ["policy_accept"]


@pytest.mark.parametrize(
    ("fn_name", "payload"),
    [
        ("evaluate_admission", {"text": "我决定启用 feature_x"}),
        ("rank_retrieval", {"query": "memory", "candidates": [{"id": "1", "status": "accepted", "score": 0.9}]}),
        ("run_self_check_specs", {"level": "L4"}),
        ("plan_self_repair", {"mode": "safe", "plan": []}),
        ("shadow_decide", {"kind": "write_takeover", "sealed": True, "seal_level": "hard", "risk": "high"}),
    ],
)
def test_open_policy_engine_p95_baseline(fn_name: str, payload: dict[str, Any]) -> None:
    eng = OpenPolicyEngine()
    fn = getattr(eng, fn_name)
    samples: list[float] = []
    for _ in range(120):
        t0 = time.perf_counter()
        _ = fn(payload)
        samples.append(time.perf_counter() - t0)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    # Conservative ceiling to catch obvious regressions without being flaky.
    assert p95 < 0.02


def test_open_self_check_specs_has_non_stub_behavior() -> None:
    eng = OpenPolicyEngine()
    out = eng.run_self_check_specs({"check_ids": ["l4_capture_trend", "unknown_check"]})
    data = out["data"]
    assert data["status"] == "warn"
    results = data["results"]
    by_id = {r["check_id"]: r["status"] for r in results}
    assert by_id["l4_capture_trend"] == "pass"
    assert by_id["unknown_check"] == "warn"


def test_open_plan_self_repair_filters_high_risk_in_safe_mode() -> None:
    eng = OpenPolicyEngine()
    out = eng.plan_self_repair(
        {
            "mode": "safe",
            "plan": [
                {"action": "rebuild_index", "risk": "low"},
                {"action": "rewrite_records", "risk": "critical"},
            ],
        }
    )
    data = out["data"]
    assert data["status"] == "ok"
    decisions = {r["action"]: r["decision"] for r in data["actions"]}
    assert decisions["rebuild_index"] == "allow"
    assert decisions["rewrite_records"] == "manual_required"


def test_open_shadow_decide_takeover_logic() -> None:
    eng = OpenPolicyEngine()
    out = eng.shadow_decide({"kind": "write_takeover", "sealed": True, "seal_level": "hard", "risk": "low"})
    assert out["data"]["takeover"] is True
    out2 = eng.shadow_decide({"kind": "write_takeover", "sealed": False, "seal_level": "soft", "risk": "high"})
    assert out2["data"]["takeover"] is False
