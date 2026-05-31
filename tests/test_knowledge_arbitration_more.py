from __future__ import annotations

from ms8.engine_core.knowledge_arbitration import KnowledgeArbitrator


def _cfg() -> dict:
    return {"settings": {"memory": {"knowledge_control": {}}}}


def test_arbitrate_candidate_pattern_and_thresholds() -> None:
    arb = KnowledgeArbitrator(_cfg())
    pattern = arb.arbitrate_candidate({"source_type": "pattern", "scores": {"total": 0.9}})
    assert pattern["admission_decision"] == "hold"
    assert pattern["knowledge_tier"] == "observation"
    assert pattern["usage_permission"]["inject"] == "weak"
    assert pattern["usage_permission"]["speak"] == "hint"

    core = arb.arbitrate_candidate({"source_type": "synthetic", "scores": {"total": 0.95}})
    assert core["knowledge_tier"] == "core"
    assert core["admission_decision"] == "admit"

    graph = arb.arbitrate_candidate({"source_type": "graph", "scores": {"total": 0.83}})
    assert graph["knowledge_tier"] == "graph"
    assert graph["admission_decision"] == "admit"

    short_term = arb.arbitrate_candidate({"source_type": "synthetic", "scores": {"total": 0.7}})
    assert short_term["knowledge_tier"] == "short_term"
    assert short_term["admission_decision"] == "hold"

    reject = arb.arbitrate_candidate({"source_type": "synthetic", "scores": {"total": 0.1}})
    assert reject["knowledge_tier"] == "rejected"
    assert reject["admission_decision"] == "reject"


def test_arbitrate_retrieval_adjustments() -> None:
    arb = KnowledgeArbitrator(_cfg())
    hard = arb.arbitrate_retrieval(
        {
            "scores": {"trust": 0.8, "fusion": 1.2},
            "signals": {"search_type": "hybrid"},
            "raw": {"governance": {"stale": False, "duplicate_mentions": 0}},
        }
    )
    assert hard["trust_level"] == "hard_trust"
    assert hard["knowledge_tier"] == "core"

    iso = arb.arbitrate_retrieval(
        {
            "scores": {"trust": 0.2, "fusion": 0.2},
            "signals": {"search_type": "hybrid_graph"},
            "raw": {"governance": {"stale": True, "duplicate_mentions": 9}},
        }
    )
    assert iso["trust_level"] in {"isolated", "hypothesis"}
    assert 0.0 <= iso["trust_score_adjusted"] <= 1.0
