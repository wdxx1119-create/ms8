"""Deterministic public metrics for Hybrid Retrieval v1 acceptance.

The evaluator is deliberately model-free. It consumes synthetic relevance judgments
and normalized observations produced by a profile runner, then emits a stable report
that can be compared across macOS and Windows.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from types import MappingProxyType
from typing import Any

EVALUATION_SCHEMA = "ms8.hybrid_evaluation.v1"


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _unique_text(values: Sequence[object], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_required_text(value, f"{field_name}[]") for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def _finite_non_negative(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return number


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    text: str
    purpose: str
    slice_name: str
    relevance: Mapping[str, int]
    expected_current: tuple[str, ...] = ()
    expected_historical: tuple[str, ...] = ()
    expected_conflicts: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    expected_degraded_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _required_text(self.case_id, "case.case_id"))
        object.__setattr__(self, "text", _required_text(self.text, "case.text"))
        object.__setattr__(self, "purpose", _required_text(self.purpose, "case.purpose"))
        object.__setattr__(self, "slice_name", _required_text(self.slice_name, "case.slice_name"))
        normalized_relevance: dict[str, int] = {}
        for claim_id, raw_grade in self.relevance.items():
            key = _required_text(claim_id, "case.relevance key")
            if isinstance(raw_grade, bool) or not isinstance(raw_grade, int) or raw_grade < 0:
                raise ValueError(f"case.relevance[{key}] must be a non-negative integer")
            normalized_relevance[key] = raw_grade
        object.__setattr__(self, "relevance", MappingProxyType(dict(sorted(normalized_relevance.items()))))
        for field_name in (
            "expected_current",
            "expected_historical",
            "expected_conflicts",
            "forbidden_claims",
            "expected_degraded_sources",
        ):
            object.__setattr__(self, field_name, _unique_text(getattr(self, field_name), f"case.{field_name}"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> EvaluationCase:
        relevance = raw.get("relevance", {})
        if not isinstance(relevance, Mapping):
            raise TypeError("case.relevance must be an object")

        def values(name: str) -> tuple[str, ...]:
            candidate = raw.get(name, ())
            if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes, bytearray)):
                raise TypeError(f"case.{name} must be an array")
            return tuple(str(value) for value in candidate)

        return cls(
            case_id=str(raw.get("id") or raw.get("case_id") or ""),
            text=str(raw.get("text") or ""),
            purpose=str(raw.get("purpose") or "recall"),
            slice_name=str(raw.get("slice") or raw.get("slice_name") or "general"),
            relevance={str(key): int(value) for key, value in relevance.items()},
            expected_current=values("expected_current"),
            expected_historical=values("expected_historical"),
            expected_conflicts=values("expected_conflicts"),
            forbidden_claims=values("forbidden_claims"),
            expected_degraded_sources=values("expected_degraded_sources"),
        )


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
    case_id: str
    ranked_claim_ids: tuple[str, ...]
    traceable_claim_ids: tuple[str, ...] = ()
    presented_conflict_claim_ids: tuple[str, ...] = ()
    degraded_sources: tuple[str, ...] = ()
    latency_ms: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _required_text(self.case_id, "observation.case_id"))
        for field_name in (
            "ranked_claim_ids",
            "traceable_claim_ids",
            "presented_conflict_claim_ids",
            "degraded_sources",
        ):
            object.__setattr__(
                self,
                field_name,
                _unique_text(getattr(self, field_name), f"observation.{field_name}"),
            )
        object.__setattr__(
            self,
            "latency_ms",
            _finite_non_negative(self.latency_ms, "observation.latency_ms"),
        )


def ndcg_at_k(ranked_claim_ids: Sequence[str], relevance: Mapping[str, int], k: int) -> float:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")

    def dcg(grades: Sequence[int]) -> float:
        return sum((2.0**grade - 1.0) / math.log2(index + 2.0) for index, grade in enumerate(grades))

    observed = [int(relevance.get(claim_id, 0)) for claim_id in ranked_claim_ids[:k]]
    ideal = sorted((int(value) for value in relevance.values()), reverse=True)[:k]
    denominator = dcg(ideal)
    return 0.0 if denominator <= 0.0 else dcg(observed) / denominator


def reciprocal_rank(ranked_claim_ids: Sequence[str], relevance: Mapping[str, int]) -> float:
    for index, claim_id in enumerate(ranked_claim_ids, start=1):
        if int(relevance.get(claim_id, 0)) > 0:
            return 1.0 / index
    return 0.0


def recall_at_k(ranked_claim_ids: Sequence[str], relevance: Mapping[str, int], k: int) -> float:
    relevant = {claim_id for claim_id, grade in relevance.items() if int(grade) > 0}
    if not relevant:
        return 1.0
    retrieved = set(ranked_claim_ids[:k])
    return len(relevant.intersection(retrieved)) / len(relevant)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _accuracy(expected: Sequence[str], ranked: Sequence[str]) -> tuple[int, int]:
    return len(set(expected).intersection(ranked)), len(expected)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def evaluate_profile(
    cases: Sequence[EvaluationCase],
    observations: Sequence[EvaluationObservation],
) -> dict[str, Any]:
    by_id = {observation.case_id: observation for observation in observations}
    if len(by_id) != len(observations):
        raise ValueError("observations must contain unique case identifiers")
    missing = [case.case_id for case in cases if case.case_id not in by_id]
    extra = sorted(set(by_id).difference(case.case_id for case in cases))
    if missing or extra:
        raise ValueError(f"case/observation mismatch: missing={missing} extra={extra}")

    ndcg5_values: list[float] = []
    ndcg10_values: list[float] = []
    reciprocal_values: list[float] = []
    recall20_values: list[float] = []
    latencies: list[float] = []
    current_hits = current_total = 0
    historical_hits = historical_total = 0
    conflict_hits = conflict_total = 0
    traceable_hits = relevant_retrieved = 0
    forbidden_hits = forbidden_total = 0
    degradation_hits = degradation_total = 0
    slice_values: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"ndcg_at_10": [], "mrr": [], "recall_at_20": []}
    )
    case_rows: list[dict[str, Any]] = []

    for case in cases:
        observation = by_id[case.case_id]
        ranked = observation.ranked_claim_ids
        ndcg5 = ndcg_at_k(ranked, case.relevance, 5)
        ndcg10 = ndcg_at_k(ranked, case.relevance, 10)
        mrr = reciprocal_rank(ranked, case.relevance)
        recall20 = recall_at_k(ranked, case.relevance, 20)
        ndcg5_values.append(ndcg5)
        ndcg10_values.append(ndcg10)
        reciprocal_values.append(mrr)
        recall20_values.append(recall20)
        latencies.append(observation.latency_ms)
        slice_values[case.slice_name]["ndcg_at_10"].append(ndcg10)
        slice_values[case.slice_name]["mrr"].append(mrr)
        slice_values[case.slice_name]["recall_at_20"].append(recall20)

        hits, total = _accuracy(case.expected_current, ranked)
        current_hits += hits
        current_total += total
        hits, total = _accuracy(case.expected_historical, ranked)
        historical_hits += hits
        historical_total += total
        hits, total = _accuracy(case.expected_conflicts, observation.presented_conflict_claim_ids)
        conflict_hits += hits
        conflict_total += total

        retrieved_relevant = {
            claim_id for claim_id in ranked if int(case.relevance.get(claim_id, 0)) > 0
        }
        relevant_retrieved += len(retrieved_relevant)
        traceable_hits += len(retrieved_relevant.intersection(observation.traceable_claim_ids))

        forbidden_hits += len(set(case.forbidden_claims).intersection(ranked))
        forbidden_total += len(case.forbidden_claims)
        degradation_hits += len(
            set(case.expected_degraded_sources).intersection(observation.degraded_sources)
        )
        degradation_total += len(case.expected_degraded_sources)

        case_rows.append(
            {
                "case_id": case.case_id,
                "slice": case.slice_name,
                "ndcg_at_5": ndcg5,
                "ndcg_at_10": ndcg10,
                "mrr": mrr,
                "recall_at_20": recall20,
                "ranked_claim_ids": list(ranked),
                "latency_ms": observation.latency_ms,
            }
        )

    slice_report = {
        name: {metric: _mean(values) for metric, values in metrics.items()}
        for name, metrics in sorted(slice_values.items())
    }
    return {
        "schema": EVALUATION_SCHEMA,
        "case_count": len(cases),
        "metrics": {
            "ndcg_at_5": _mean(ndcg5_values),
            "ndcg_at_10": _mean(ndcg10_values),
            "mrr": _mean(reciprocal_values),
            "recall_at_20": _mean(recall20_values),
            "current_fact_accuracy": 1.0 if current_total == 0 else current_hits / current_total,
            "historical_fact_accuracy": 1.0 if historical_total == 0 else historical_hits / historical_total,
            "evidence_citation_coverage": (
                1.0 if relevant_retrieved == 0 else traceable_hits / relevant_retrieved
            ),
            "conflict_presentation_rate": 1.0 if conflict_total == 0 else conflict_hits / conflict_total,
            "unauthorized_inactive_error_recall_rate": (
                0.0 if forbidden_total == 0 else forbidden_hits / forbidden_total
            ),
            "degradation_correctness": (
                1.0 if degradation_total == 0 else degradation_hits / degradation_total
            ),
            "latency_ms_p50": median(latencies) if latencies else 0.0,
            "latency_ms_p95": _percentile(latencies, 0.95),
        },
        "slices": slice_report,
        "cases": case_rows,
    }


def compare_profiles(
    cases: Sequence[EvaluationCase],
    baseline: Sequence[EvaluationObservation],
    hybrid: Sequence[EvaluationObservation],
) -> dict[str, Any]:
    baseline_report = evaluate_profile(cases, baseline)
    hybrid_report = evaluate_profile(cases, hybrid)
    baseline_metrics = baseline_report["metrics"]
    hybrid_metrics = hybrid_report["metrics"]
    baseline_ndcg = float(baseline_metrics["ndcg_at_10"])
    hybrid_ndcg = float(hybrid_metrics["ndcg_at_10"])
    relative_improvement = (
        0.0 if baseline_ndcg <= 0.0 else (hybrid_ndcg - baseline_ndcg) / baseline_ndcg
    )
    return {
        "schema": EVALUATION_SCHEMA,
        "baseline": baseline_report,
        "hybrid": hybrid_report,
        "comparison": {
            "ndcg_at_10_absolute_delta": hybrid_ndcg - baseline_ndcg,
            "ndcg_at_10_relative_improvement": relative_improvement,
            "recall_at_20_delta": float(hybrid_metrics["recall_at_20"])
            - float(baseline_metrics["recall_at_20"]),
        },
    }


__all__ = [
    "EVALUATION_SCHEMA",
    "EvaluationCase",
    "EvaluationObservation",
    "compare_profiles",
    "evaluate_profile",
    "ndcg_at_k",
    "recall_at_k",
    "reciprocal_rank",
]
