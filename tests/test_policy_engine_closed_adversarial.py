from __future__ import annotations

import time


def _import_closed_engine():
    from ms8_policy_core.engine import ClosedPolicyEngine

    return ClosedPolicyEngine


def test_closed_admission_blocks_noise_and_ack() -> None:
    ClosedPolicyEngine = _import_closed_engine()
    eng = ClosedPolicyEngine()
    out = eng.evaluate_admission({"text": "ok"})
    assert out["data"]["route"] in {"rejected", "short_term_only"}


def test_closed_admission_redacts_token() -> None:
    ClosedPolicyEngine = _import_closed_engine()
    eng = ClosedPolicyEngine()
    text = "my key is sk-proj-ABCDEFGH1234567890"
    out = eng.evaluate_admission({"text": text})
    assert out["data"]["route"] in {"redacted_accept", "pending_review"}
    assert "privacy_hit" in out["data"]["reasons"]


def test_closed_admission_pending_on_conflict() -> None:
    ClosedPolicyEngine = _import_closed_engine()
    eng = ClosedPolicyEngine()
    text = "这个配置要启用也要禁用"
    out = eng.evaluate_admission({"text": text})
    assert out["data"]["route"] == "pending_review"


def test_closed_retrieval_filters_injection_policy() -> None:
    ClosedPolicyEngine = _import_closed_engine()
    eng = ClosedPolicyEngine()
    payload = {
        "candidates": [
            {"id": "a", "status": "revoked", "usage_permission": {"inject": "primary", "speak": "primary"}},
            {"id": "b", "scope": "system_debug", "usage_permission": {"inject": "weak", "speak": "hint"}},
            {"id": "c", "trust_level": "hard_trust", "usage_permission": {"inject": "primary", "speak": "primary"}},
        ],
        "budget": {"budget_top_k": 3},
    }
    out = eng.rank_retrieval(payload)
    ids = [x["id"] for x in out["data"]["items"]]
    assert ids == ["c"]
    assert len(out["data"]["blocked"]) == 2


def test_closed_admission_high_risk_secret_pending_review() -> None:
    ClosedPolicyEngine = _import_closed_engine()
    eng = ClosedPolicyEngine()
    text = "password=SuperSecret123"
    out = eng.evaluate_admission({"text": text})
    assert out["data"]["route"] == "pending_review"
    assert "privacy_hit" in out["data"]["reasons"]
    assert "password_field" in out["data"]["privacy_flags"] or "password_cn" in out["data"]["privacy_flags"]


def test_closed_retrieval_blocks_revoked_even_if_primary_inject() -> None:
    ClosedPolicyEngine = _import_closed_engine()
    eng = ClosedPolicyEngine()
    payload = {
        "candidates": [
            {
                "id": "revoked-primary",
                "status": "revoked",
                "usage_permission": {"inject": "primary", "speak": "primary"},
                "trust_level": "hard_trust",
            },
            {
                "id": "accepted-primary",
                "status": "accepted",
                "usage_permission": {"inject": "primary", "speak": "primary"},
                "trust_level": "hard_trust",
            },
        ],
        "budget": {"budget_top_k": 5},
    }
    out = eng.rank_retrieval(payload)
    ids = [x["id"] for x in out["data"]["items"]]
    assert ids == ["accepted-primary"]
    blocked_reasons = {x["id"]: x["reason"] for x in out["data"]["blocked"]}
    assert blocked_reasons.get("revoked-primary") == "status_revoked"


def test_closed_policy_engine_p95_baseline() -> None:
    ClosedPolicyEngine = _import_closed_engine()
    eng = ClosedPolicyEngine()
    samples: list[float] = []
    payload = {"text": "我们决定在生产环境启用新阈值并保留回滚策略"}
    for _ in range(120):
        t0 = time.perf_counter()
        _ = eng.evaluate_admission(payload)
        samples.append(time.perf_counter() - t0)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    assert p95 < 0.02
