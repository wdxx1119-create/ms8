from __future__ import annotations

from ms8.engine_core.policy_engine_open import OpenPolicyEngine


def _assert_envelope(payload: dict) -> None:
    assert isinstance(payload, dict)
    for key in ("ok", "code", "reason", "trace_id", "data"):
        assert key in payload
    assert isinstance(payload["ok"], bool)
    assert isinstance(payload["code"], str)
    assert isinstance(payload["reason"], str)
    assert isinstance(payload["trace_id"], str)
    assert isinstance(payload["data"], dict)


def test_open_backend_contract_all_methods() -> None:
    engine = OpenPolicyEngine()
    methods = [
        engine.evaluate_admission,
        engine.rank_retrieval,
        engine.run_self_check_specs,
        engine.plan_self_repair,
        engine.shadow_decide,
    ]
    for method in methods:
        result = method({"text": "hello", "candidates": []})
        _assert_envelope(result)
