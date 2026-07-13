"""Versioned weighted RRF and deterministic governed reranking."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from ..application.replay import ReplayState
from ..domain.models import Claim, Evidence
from .adapters import CandidateBatch
from .eligibility import EligibleClaims, normalize_authority
from .models import CandidateHit, RankedClaim, RetrievalPlan

FUSION_CONFIG_SCHEMA = "ms8.hybrid_fusion.v1"

_DEFAULT_CHANNEL_WEIGHTS = {
    "lexical": 1.0,
    "vector": 1.0,
    "entity": 0.9,
    "temporal": 1.1,
    "graph": 0.8,
}
_DEFAULT_SIGNAL_WEIGHTS = {
    "fused_retrieval": 0.34,
    "authority": 0.14,
    "evidence_strength": 0.13,
    "temporal_currentness": 0.10,
    "scope_intent": 0.10,
    "status_verification": 0.08,
    "conflict_handling": 0.06,
    "type_freshness": 0.05,
}
_AUTHORITY_SCORES = {
    "user_explicit": 1.0,
    "reviewer_verified": 0.95,
    "user_implicit": 0.82,
    "system_observed": 0.70,
    "migration": 0.60,
    "tool_generated": 0.55,
    "agent_inferred": 0.35,
}
_STATUS_SCORES = {
    "verified": 1.0,
    "accepted": 0.86,
    "disputed": 0.45,
    "proposed": 0.25,
    "superseded": 0.20,
    "expired": 0.15,
}
_TYPE_HALF_LIFE_DAYS = {
    "task": 30.0,
    "decision": 180.0,
    "constraint": 365.0,
    "summary": 365.0,
    "preference": 730.0,
    "fact": 1460.0,
}
_INFERRED_AUTHORITIES = frozenset(
    {
        "agent_inferred",
        "assistant_inferred",
        "model_inferred",
        "llm_inferred",
    }
)
_EXPLICIT_AUTHORITIES = frozenset({"user_explicit", "reviewer_verified"})


def _finite_non_negative(value: object, field_name: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return number


def _freeze_float_mapping(
    values: Mapping[str, object],
    field_name: str,
    *,
    required_keys: frozenset[str],
) -> Mapping[str, float]:
    normalized: dict[str, float] = {}
    for key, value in values.items():
        name = str(key or "").strip()
        if not name:
            raise ValueError(f"{field_name} keys must not be empty")
        normalized[name] = _finite_non_negative(value, f"{field_name}.{name}")
    missing = required_keys.difference(normalized)
    extra = set(normalized).difference(required_keys)
    if missing or extra:
        raise ValueError(
            f"{field_name} keys mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, slots=True)
class FusionConfig:
    """Versioned deterministic fusion configuration."""

    schema: str = FUSION_CONFIG_SCHEMA
    rrf_k: int = 60
    channel_weights: Mapping[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_CHANNEL_WEIGHTS)
    )
    signal_weights: Mapping[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_SIGNAL_WEIGHTS)
    )

    def __post_init__(self) -> None:
        schema = str(self.schema or "").strip()
        if schema != FUSION_CONFIG_SCHEMA:
            raise ValueError(f"unsupported fusion config schema: {schema or '<empty>'}")
        object.__setattr__(self, "schema", schema)
        if isinstance(self.rrf_k, bool) or not isinstance(self.rrf_k, int) or self.rrf_k < 1:
            raise ValueError("fusion.rrf_k must be a positive integer")
        object.__setattr__(
            self,
            "channel_weights",
            _freeze_float_mapping(
                self.channel_weights,
                "fusion.channel_weights",
                required_keys=frozenset(_DEFAULT_CHANNEL_WEIGHTS),
            ),
        )
        signal_weights = _freeze_float_mapping(
            self.signal_weights,
            "fusion.signal_weights",
            required_keys=frozenset(_DEFAULT_SIGNAL_WEIGHTS),
        )
        total = sum(signal_weights.values())
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("fusion.signal_weights must sum to 1.0")
        object.__setattr__(self, "signal_weights", signal_weights)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "rrf_k": self.rrf_k,
            "channel_weights": dict(self.channel_weights),
            "signal_weights": dict(self.signal_weights),
        }


@dataclass(frozen=True, slots=True)
class FusionResult:
    """Deterministic ranked output plus the configuration identity used."""

    config_schema: str
    ranked_claims: tuple[RankedClaim, ...]
    source_names: tuple[str, ...]
    candidate_count: int

    def __post_init__(self) -> None:
        if self.config_schema != FUSION_CONFIG_SCHEMA:
            raise ValueError("fusion result schema mismatch")
        if any(not isinstance(item, RankedClaim) for item in self.ranked_claims):
            raise TypeError("fusion result must contain RankedClaim values")
        names = tuple(str(value or "").strip() for value in self.source_names)
        if any(not value for value in names) or len(set(names)) != len(names):
            raise ValueError("fusion result source_names must be unique non-empty strings")
        object.__setattr__(self, "source_names", tuple(sorted(names)))
        if (
            isinstance(self.candidate_count, bool)
            or not isinstance(self.candidate_count, int)
            or self.candidate_count < 0
        ):
            raise ValueError("fusion result candidate_count must be non-negative")
        if len(self.ranked_claims) != self.candidate_count:
            raise ValueError("fusion result candidate_count mismatch")


@dataclass(frozen=True, slots=True)
class _Aggregate:
    claim_id: str
    hits_by_source: Mapping[str, CandidateHit]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Scored:
    ranked: RankedClaim
    precedence_group: tuple[str, str, str, str]
    authority_class: str


def _deduplicate(batch: CandidateBatch, eligible: EligibleClaims) -> tuple[_Aggregate, ...]:
    by_claim: dict[str, dict[str, CandidateHit]] = defaultdict(dict)
    evidence_by_claim: dict[str, set[str]] = defaultdict(set)

    for source_name in sorted(batch.hits_by_source):
        hits = sorted(
            batch.hits_by_source[source_name],
            key=lambda item: (item.rank, -item.raw_score, item.claim_id, item.evidence_ids),
        )
        for hit in hits:
            eligible.require(hit.claim_id)
            previous = by_claim[hit.claim_id].get(source_name)
            if previous is None or (hit.rank, -hit.raw_score, hit.evidence_ids) < (
                previous.rank,
                -previous.raw_score,
                previous.evidence_ids,
            ):
                by_claim[hit.claim_id][source_name] = hit
            evidence_by_claim[hit.claim_id].update(hit.evidence_ids)

    return tuple(
        _Aggregate(
            claim_id=claim_id,
            hits_by_source=MappingProxyType(dict(sorted(by_claim[claim_id].items()))),
            evidence_ids=tuple(sorted(evidence_by_claim[claim_id])),
        )
        for claim_id in sorted(by_claim)
    )


def _rrf_score(aggregate: _Aggregate, config: FusionConfig) -> float:
    score = 0.0
    for hit in aggregate.hits_by_source.values():
        weight = config.channel_weights[hit.channel]
        score += weight / float(config.rrf_k + hit.rank)
    return score


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _source_key(state: ReplayState, evidence: Evidence) -> str:
    event = state.memory_events.get(evidence.event_id)
    if event is not None and event.source:
        payload: object = event.source
    else:
        preferred = {
            key: value
            for key, value in evidence.fragment.items()
            if str(key).casefold()
            in {
                "source",
                "source_id",
                "document_id",
                "path",
                "url",
                "uri",
                "provider",
                "repository",
            }
        }
        payload = preferred or {"event_id": evidence.event_id}
    encoded = json.dumps(
        _jsonable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "source:" + hashlib.sha256(encoded).hexdigest()


def _evidence_strength(
    state: ReplayState,
    aggregate: _Aggregate,
) -> tuple[float, int, int]:
    positive_by_source: dict[str, float] = {}
    contradiction_sources: set[str] = set()
    for evidence_id in aggregate.evidence_ids:
        evidence = state.evidence.get(evidence_id)
        if evidence is None:
            raise ValueError(
                f"fusion candidate references missing evidence: {aggregate.claim_id}:{evidence_id}"
            )
        if evidence.claim_id != aggregate.claim_id:
            raise ValueError(
                f"fusion candidate evidence belongs to another claim: {aggregate.claim_id}:{evidence_id}"
            )
        source_key = _source_key(state, evidence)
        if evidence.relation == "contradicts":
            contradiction_sources.add(source_key)
            continue
        if evidence.relation not in {"supports", "clarifies", "supersedes_basis"}:
            continue
        positive_by_source[source_key] = max(
            positive_by_source.get(source_key, 0.0),
            min(1.0, evidence.weight),
        )
    positive = sum(positive_by_source.values())
    strength = min(1.0, positive / 3.0)
    return strength, len(positive_by_source), len(contradiction_sources)


def _authority_class(claim: Claim, current_status: str) -> str:
    authority = normalize_authority(claim.authority).casefold()
    if authority in _INFERRED_AUTHORITIES or authority.endswith("_inferred"):
        return "inferred"
    if authority in _EXPLICIT_AUTHORITIES:
        return "authoritative"
    if current_status == "verified" and claim.scope.casefold().startswith("project:"):
        return "authoritative"
    return "neutral"


def _authority_score(claim: Claim) -> float:
    authority = normalize_authority(claim.authority).casefold()
    if authority.endswith("_inferred"):
        return _AUTHORITY_SCORES["agent_inferred"]
    return _AUTHORITY_SCORES.get(authority, 0.60)


def _temporal_currentness(plan: RetrievalPlan, current_status: str) -> float:
    historical = plan.query.purpose == "historical" or plan.intent == "historical_reason"
    if historical:
        return 1.0 if current_status in {"superseded", "expired"} else 0.35
    return 1.0 if current_status in {"accepted", "verified", "disputed"} else 0.0


def _intent_kind_score(intent: str, claim_kind: str) -> float:
    preferred = {
        "current_state": {"fact", "task", "decision", "constraint"},
        "historical_reason": {"decision", "fact", "constraint"},
        "project_rule": {"constraint", "decision", "fact"},
        "personal_preference": {"preference"},
        "code_symbol": {"fact", "task", "constraint"},
        "open_recall": {"preference", "constraint", "decision", "fact", "task", "summary"},
    }
    return 1.0 if claim_kind in preferred[intent] else 0.45


def _scope_intent_score(plan: RetrievalPlan, claim: Claim) -> float:
    if plan.query.scope is not None:
        scope_score = 1.0 if claim.scope == plan.query.scope else 0.0
    elif claim.scope in plan.principal.scopes:
        scope_score = 0.90
    else:
        scope_score = 0.65
    return (scope_score + _intent_kind_score(plan.intent, claim.kind)) / 2.0


def _status_score(plan: RetrievalPlan, current_status: str) -> float:
    historical = plan.query.purpose == "historical" or plan.intent == "historical_reason"
    if historical and current_status in {"superseded", "expired"}:
        return 0.88
    return _STATUS_SCORES.get(current_status, 0.0)


def _conflict_ids(state: ReplayState, claim_id: str) -> tuple[str, ...]:
    result: list[str] = []
    for conflict_id in sorted(state.conflicts):
        conflict = state.conflicts[conflict_id]
        raw_claim_ids = conflict.get("claim_ids", ())
        claim_ids = (
            tuple(str(value) for value in raw_claim_ids)
            if isinstance(raw_claim_ids, Sequence)
            and not isinstance(raw_claim_ids, (str, bytes, bytearray))
            else ()
        )
        if claim_id not in claim_ids:
            continue
        status = str(conflict.get("status") or "").casefold()
        resolved = conflict.get("resolved") is True or status in {"resolved", "closed"}
        if not resolved:
            result.append(conflict_id)
    return tuple(result)


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _reference_time(state: ReplayState, plan: RetrievalPlan) -> datetime | None:
    for value in (
        plan.query.time.valid_at,
        plan.query.time.observed_as_of,
        plan.query.time.recorded_as_of,
    ):
        parsed = _parse_time(value)
        if parsed is not None:
            return parsed
    values: list[datetime] = []
    for event in state.memory_events.values():
        parsed = _parse_time(event.observed_at)
        if parsed is not None:
            values.append(parsed)
    for decision in state.decisions.values():
        parsed = _parse_time(decision.recorded_at)
        if parsed is not None:
            values.append(parsed)
    for view in state.claims.values():
        for value in (view.claim.valid_time.start, view.claim.valid_time.end):
            parsed = _parse_time(value)
            if parsed is not None:
                values.append(parsed)
    return max(values) if values else None


def _freshness_score(state: ReplayState, plan: RetrievalPlan, claim: Claim) -> float:
    historical = plan.query.purpose == "historical" or plan.intent == "historical_reason"
    if historical:
        return 0.50
    reference = _reference_time(state, plan)
    origin = _parse_time(claim.valid_time.start)
    if origin is None:
        event = state.memory_events.get(claim.created_from_event_id)
        origin = _parse_time(event.observed_at) if event is not None else None
    if reference is None or origin is None:
        return 0.50
    age_days = max(0.0, (reference - origin).total_seconds() / 86400.0)
    half_life = _TYPE_HALF_LIFE_DAYS.get(claim.kind, 365.0)
    return 1.0 / (1.0 + age_days / half_life)


def _precedence_group(claim: Claim) -> tuple[str, str, str, str]:
    return (
        claim.realm_id.casefold(),
        claim.scope.casefold(),
        claim.subject.casefold(),
        claim.predicate.casefold(),
    )


def _score_candidate(
    aggregate: _Aggregate,
    *,
    state: ReplayState,
    plan: RetrievalPlan,
    config: FusionConfig,
    rrf_max: float,
) -> _Scored:
    view = state.claims.get(aggregate.claim_id)
    if view is None:
        raise ValueError(f"fusion candidate references missing claim: {aggregate.claim_id}")
    claim = view.claim
    rrf_raw = _rrf_score(aggregate, config)
    fused = rrf_raw / rrf_max if rrf_max > 0.0 else 0.0
    evidence, independent_sources, contradiction_sources = _evidence_strength(state, aggregate)
    unresolved_conflicts = _conflict_ids(state, aggregate.claim_id)
    signals = {
        "fused_retrieval": fused,
        "authority": _authority_score(claim),
        "evidence_strength": evidence,
        "temporal_currentness": _temporal_currentness(plan, view.current_status),
        "scope_intent": _scope_intent_score(plan, claim),
        "status_verification": _status_score(plan, view.current_status),
        "conflict_handling": 0.25 if unresolved_conflicts else 1.0,
        "type_freshness": _freshness_score(state, plan, claim),
    }
    weighted = {
        key: signals[key] * config.signal_weights[key]
        for key in sorted(signals)
    }
    score = round(sum(weighted.values()), 12)
    authority_class = _authority_class(claim, view.current_status)
    explanation = (
        f"fusion_config={config.schema};rrf_k={config.rrf_k}",
        "rrf_sources="
        + ",".join(
            f"{source}:{hit.channel}@{hit.rank}"
            for source, hit in aggregate.hits_by_source.items()
        ),
        f"authority={normalize_authority(claim.authority)};class={authority_class}",
        f"evidence_independent_sources={independent_sources};contradiction_sources={contradiction_sources}",
        f"temporal_status={view.current_status};intent={plan.intent}",
        f"scope={claim.scope};kind={claim.kind}",
        "conflicts=" + (",".join(unresolved_conflicts) if unresolved_conflicts else "none"),
        f"freshness={signals['type_freshness']:.12f}",
    )
    components = {
        "rrf_raw": round(rrf_raw, 12),
        **{key: round(value, 12) for key, value in signals.items()},
        **{f"weighted_{key}": round(value, 12) for key, value in weighted.items()},
    }
    hard_rule_tier = 1 if authority_class == "inferred" else 0
    return _Scored(
        ranked=RankedClaim(
            claim_id=aggregate.claim_id,
            evidence_ids=aggregate.evidence_ids,
            score=score,
            hard_rule_tier=hard_rule_tier,
            score_components=components,
            explanation=explanation,
        ),
        precedence_group=_precedence_group(claim),
        authority_class=authority_class,
    )


def _apply_authority_precedence(scored: Sequence[_Scored]) -> tuple[RankedClaim, ...]:
    """Topologically enforce same-predicate authority without global score distortion."""

    by_group: dict[tuple[str, str, str, str], list[_Scored]] = defaultdict(list)
    by_claim = {item.ranked.claim_id: item for item in scored}
    for item in scored:
        by_group[item.precedence_group].append(item)

    outgoing: dict[str, set[str]] = {claim_id: set() for claim_id in by_claim}
    indegree: dict[str, int] = {claim_id: 0 for claim_id in by_claim}
    for items in by_group.values():
        authoritative = sorted(
            item.ranked.claim_id
            for item in items
            if item.authority_class == "authoritative"
        )
        inferred = sorted(
            item.ranked.claim_id
            for item in items
            if item.authority_class == "inferred"
        )
        for stronger in authoritative:
            for weaker in inferred:
                if weaker not in outgoing[stronger]:
                    outgoing[stronger].add(weaker)
                    indegree[weaker] += 1

    heap: list[tuple[float, str]] = []
    for claim_id, degree in indegree.items():
        if degree == 0:
            heapq.heappush(heap, (-by_claim[claim_id].ranked.score, claim_id))

    ordered: list[RankedClaim] = []
    while heap:
        _negative_score, claim_id = heapq.heappop(heap)
        ordered.append(by_claim[claim_id].ranked)
        for neighbor in sorted(outgoing[claim_id]):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                heapq.heappush(heap, (-by_claim[neighbor].ranked.score, neighbor))

    if len(ordered) != len(scored):
        raise RuntimeError("authority precedence graph unexpectedly contains a cycle")
    return tuple(ordered)


def fuse_and_rerank(
    batch: CandidateBatch,
    state: ReplayState,
    plan: RetrievalPlan,
    eligible: EligibleClaims,
    *,
    config: FusionConfig | None = None,
) -> FusionResult:
    """Fuse eligible candidates and rerank them with deterministic governed signals."""

    if not isinstance(batch, CandidateBatch):
        raise TypeError("batch must be CandidateBatch")
    if not isinstance(state, ReplayState):
        raise TypeError("state must be ReplayState")
    if not isinstance(plan, RetrievalPlan):
        raise TypeError("plan must be RetrievalPlan")
    if not isinstance(eligible, EligibleClaims):
        raise TypeError("eligible must be EligibleClaims")
    active_config = config or FusionConfig()
    if not isinstance(active_config, FusionConfig):
        raise TypeError("config must be FusionConfig")

    aggregates = _deduplicate(batch, eligible)
    for aggregate in aggregates:
        eligible.require(aggregate.claim_id)
    raw_scores = tuple(_rrf_score(item, active_config) for item in aggregates)
    rrf_max = max(raw_scores, default=0.0)
    scored = tuple(
        _score_candidate(
            aggregate,
            state=state,
            plan=plan,
            config=active_config,
            rrf_max=rrf_max,
        )
        for aggregate in aggregates
    )
    ranked = _apply_authority_precedence(scored)
    return FusionResult(
        config_schema=active_config.schema,
        ranked_claims=ranked,
        source_names=tuple(batch.hits_by_source),
        candidate_count=len(ranked),
    )


__all__ = [
    "FUSION_CONFIG_SCHEMA",
    "FusionConfig",
    "FusionResult",
    "fuse_and_rerank",
]
