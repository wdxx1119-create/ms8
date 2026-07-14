from __future__ import annotations

import json
from pathlib import Path

import pytest

from ms8.memory.retrieval.evaluation import (
    EVALUATION_SCHEMA,
    EvaluationCase,
    EvaluationObservation,
    compare_profiles,
    evaluate_profile,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "memory_hybrid_v1" / "public_evaluation_v1.json"


def _cases() -> tuple[EvaluationCase, ...]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return tuple(EvaluationCase.from_mapping(item) for item in payload["queries"])


def test_public_fixture_is_synthetic_versioned_and_complete() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert payload["schema"] == "ms8.hybrid_evaluation_fixture.v1"
    assert "Synthetic" in payload["description"]
    assert len(payload["claims"]) >= 8
    cases = _cases()
    assert {case.slice_name for case in cases}.issuperset(
        {"english", "historical", "chinese", "code", "conflict", "policy"}
    )
    assert any(case.forbidden_claims for case in cases)
    assert any(case.expected_degraded_sources for case in cases)


def test_ranking_metrics_are_deterministic() -> None:
    relevance = {"a": 3, "b": 2, "c": 1}

    assert ndcg_at_k(("a", "b", "c"), relevance, 3) == pytest.approx(1.0)
    assert ndcg_at_k(("c", "b", "a"), relevance, 3) < 1.0
    assert reciprocal_rank(("x", "b", "a"), relevance) == pytest.approx(0.5)
    assert recall_at_k(("a", "x", "b"), relevance, 2) == pytest.approx(1 / 3)


def test_profile_report_covers_release_metrics_and_slices() -> None:
    cases = (
        EvaluationCase(
            case_id="current",
            text="current rule",
            purpose="recall",
            slice_name="english",
            relevance={"current": 3},
            expected_current=("current",),
            forbidden_claims=("revoked",),
            expected_degraded_sources=("embedding-unavailable",),
        ),
        EvaluationCase(
            case_id="history",
            text="previous rule",
            purpose="historical",
            slice_name="historical",
            relevance={"old": 3},
            expected_historical=("old",),
            expected_conflicts=("old",),
        ),
    )
    observations = (
        EvaluationObservation(
            case_id="current",
            ranked_claim_ids=("current",),
            traceable_claim_ids=("current",),
            degraded_sources=("embedding-unavailable",),
            latency_ms=4.0,
        ),
        EvaluationObservation(
            case_id="history",
            ranked_claim_ids=("old",),
            traceable_claim_ids=("old",),
            presented_conflict_claim_ids=("old",),
            latency_ms=6.0,
        ),
    )

    report = evaluate_profile(cases, observations)

    assert report["schema"] == EVALUATION_SCHEMA
    metrics = report["metrics"]
    assert metrics["ndcg_at_5"] == pytest.approx(1.0)
    assert metrics["ndcg_at_10"] == pytest.approx(1.0)
    assert metrics["mrr"] == pytest.approx(1.0)
    assert metrics["recall_at_20"] == pytest.approx(1.0)
    assert metrics["current_fact_accuracy"] == pytest.approx(1.0)
    assert metrics["historical_fact_accuracy"] == pytest.approx(1.0)
    assert metrics["evidence_citation_coverage"] == pytest.approx(1.0)
    assert metrics["conflict_presentation_rate"] == pytest.approx(1.0)
    assert metrics["unauthorized_inactive_error_recall_rate"] == pytest.approx(0.0)
    assert metrics["degradation_correctness"] == pytest.approx(1.0)
    assert metrics["latency_ms_p50"] == pytest.approx(5.0)
    assert metrics["latency_ms_p95"] == pytest.approx(5.9)
    assert set(report["slices"]) == {"english", "historical"}


def test_profile_comparison_reports_relative_gain_and_recall_delta() -> None:
    cases = (
        EvaluationCase(
            case_id="q1",
            text="rule",
            purpose="recall",
            slice_name="english",
            relevance={"best": 3, "secondary": 1},
        ),
    )
    baseline = (
        EvaluationObservation(
            case_id="q1",
            ranked_claim_ids=("secondary", "best"),
            traceable_claim_ids=("secondary", "best"),
        ),
    )
    hybrid = (
        EvaluationObservation(
            case_id="q1",
            ranked_claim_ids=("best", "secondary"),
            traceable_claim_ids=("best", "secondary"),
        ),
    )

    report = compare_profiles(cases, baseline, hybrid)

    assert report["comparison"]["ndcg_at_10_absolute_delta"] > 0.0
    assert report["comparison"]["ndcg_at_10_relative_improvement"] > 0.0
    assert report["comparison"]["recall_at_20_delta"] == pytest.approx(0.0)


def test_case_and_observation_contracts_fail_closed() -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        EvaluationCase(
            case_id="bad",
            text="bad",
            purpose="recall",
            slice_name="general",
            relevance={"claim": -1},
        )
    with pytest.raises(ValueError, match="duplicates"):
        EvaluationObservation(
            case_id="bad",
            ranked_claim_ids=("claim", "claim"),
        )
