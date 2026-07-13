"""Immutable contracts for governed Hybrid Retrieval v1.

The contracts in this module are intentionally independent from concrete search,
embedding, graph, or ranking implementations.  Candidate sources must exchange
claim identifiers through these objects and must never treat raw files or chunks
as authoritative final retrieval results.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Literal, cast

RetrievalPurpose = Literal["recall", "prepare_reply", "inject", "historical", "review", "audit"]
QueryIntent = Literal[
    "current_state",
    "historical_reason",
    "project_rule",
    "personal_preference",
    "code_symbol",
    "open_recall",
]
CandidateChannel = Literal["lexical", "vector", "entity", "temporal", "graph"]
PrincipalKind = Literal["user", "agent", "mcp_client", "system", "reviewer"]

_RETRIEVAL_PURPOSES = frozenset({"recall", "prepare_reply", "inject", "historical", "review", "audit"})
_QUERY_INTENTS = frozenset(
    {
        "current_state",
        "historical_reason",
        "project_rule",
        "personal_preference",
        "code_symbol",
        "open_recall",
    }
)
_CANDIDATE_CHANNELS = frozenset({"lexical", "vector", "entity", "temporal", "graph"})
_PRINCIPAL_KINDS = frozenset({"user", "agent", "mcp_client", "system", "reviewer"})


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _unique_text_tuple(values: Sequence[object], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_required_text(value, f"{field_name}[]") for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def _utc_iso(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    raw = _required_text(value, field_name)
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_score(value: object, field_name: str) -> float:
    try:
        score = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(score):
        raise ValueError(f"{field_name} must be finite")
    return score


def _freeze_mapping(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{field_name} keys must be strings")
        normalized[key] = item
    return MappingProxyType(normalized)


@dataclass(frozen=True, slots=True)
class Principal:
    """Identity and explicit retrieval boundary supplied by the caller."""

    principal_id: str
    kind: PrincipalKind
    realm_ids: tuple[str, ...]
    scopes: tuple[str, ...] = field(default_factory=tuple)
    allowed_sensitivities: tuple[str, ...] = ("public", "internal", "private")
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "principal_id", _required_text(self.principal_id, "principal_id"))
        kind = _required_text(self.kind, "principal.kind")
        if kind not in _PRINCIPAL_KINDS:
            raise ValueError(f"unsupported principal.kind: {kind}")
        object.__setattr__(self, "kind", kind)
        realms = _unique_text_tuple(self.realm_ids, "principal.realm_ids")
        if not realms:
            raise ValueError("principal.realm_ids must not be empty")
        object.__setattr__(self, "realm_ids", realms)
        object.__setattr__(self, "scopes", _unique_text_tuple(self.scopes, "principal.scopes"))
        sensitivities = _unique_text_tuple(self.allowed_sensitivities, "principal.allowed_sensitivities")
        if not sensitivities:
            raise ValueError("principal.allowed_sensitivities must not be empty")
        object.__setattr__(self, "allowed_sensitivities", sensitivities)
        object.__setattr__(self, "capabilities", _unique_text_tuple(self.capabilities, "principal.capabilities"))


@dataclass(frozen=True, slots=True)
class TimeCoordinates:
    """Independent Ledger time coordinates used by retrieval."""

    recorded_as_of: str | None = None
    observed_as_of: str | None = None
    valid_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "recorded_as_of", _utc_iso(self.recorded_as_of, "recorded_as_of"))
        object.__setattr__(self, "observed_as_of", _utc_iso(self.observed_as_of, "observed_as_of"))
        object.__setattr__(self, "valid_at", _utc_iso(self.valid_at, "valid_at"))

    @classmethod
    def from_as_of(cls, as_of: str) -> TimeCoordinates:
        """Expand a convenience ``as_of`` value across all three coordinates."""

        normalized = _utc_iso(as_of, "as_of")
        return cls(recorded_as_of=normalized, observed_as_of=normalized, valid_at=normalized)


@dataclass(frozen=True, slots=True)
class CandidateLimits:
    lexical: int = 100
    vector: int = 100
    entity: int = 50
    temporal: int = 50
    graph: int = 50

    def __post_init__(self) -> None:
        for field_name in ("lexical", "vector", "entity", "temporal", "graph"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"candidate_limits.{field_name} must be a positive integer")

    def for_channel(self, channel: CandidateChannel) -> int:
        if channel not in _CANDIDATE_CHANNELS:
            raise ValueError(f"unsupported candidate channel: {channel}")
        return int(getattr(self, channel))


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    text: str
    purpose: RetrievalPurpose = "recall"
    time: TimeCoordinates = field(default_factory=TimeCoordinates)
    realm_ids: tuple[str, ...] = field(default_factory=tuple)
    scope: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _required_text(self.text, "query.text"))
        purpose = _required_text(self.purpose, "query.purpose")
        if purpose not in _RETRIEVAL_PURPOSES:
            raise ValueError(f"unsupported retrieval purpose: {purpose}")
        object.__setattr__(self, "purpose", purpose)
        if not isinstance(self.time, TimeCoordinates):
            raise TypeError("query.time must be TimeCoordinates")
        object.__setattr__(self, "realm_ids", _unique_text_tuple(self.realm_ids, "query.realm_ids"))
        if self.scope is not None:
            object.__setattr__(self, "scope", _required_text(self.scope, "query.scope"))


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    query: MemoryQuery
    principal: Principal
    intent: QueryIntent
    realm_ids: tuple[str, ...]
    language_profile: tuple[str, ...] = ("zh", "en", "code")
    entity_mentions: tuple[str, ...] = field(default_factory=tuple)
    candidate_limits: CandidateLimits = field(default_factory=CandidateLimits)
    context_budget_tokens: int = 1200

    def __post_init__(self) -> None:
        if not isinstance(self.query, MemoryQuery):
            raise TypeError("plan.query must be MemoryQuery")
        if not isinstance(self.principal, Principal):
            raise TypeError("plan.principal must be Principal")
        intent = _required_text(self.intent, "plan.intent")
        if intent not in _QUERY_INTENTS:
            raise ValueError(f"unsupported query intent: {intent}")
        object.__setattr__(self, "intent", intent)
        realms = _unique_text_tuple(self.realm_ids, "plan.realm_ids")
        if not realms:
            raise ValueError("plan.realm_ids must not be empty")
        unauthorized = set(realms).difference(self.principal.realm_ids)
        if unauthorized:
            raise ValueError("plan.realm_ids must be a subset of principal.realm_ids")
        object.__setattr__(self, "realm_ids", realms)
        languages = _unique_text_tuple(self.language_profile, "plan.language_profile")
        if not languages:
            raise ValueError("plan.language_profile must not be empty")
        object.__setattr__(self, "language_profile", languages)
        object.__setattr__(self, "entity_mentions", _unique_text_tuple(self.entity_mentions, "plan.entity_mentions"))
        if not isinstance(self.candidate_limits, CandidateLimits):
            raise TypeError("plan.candidate_limits must be CandidateLimits")
        if (
            isinstance(self.context_budget_tokens, bool)
            or not isinstance(self.context_budget_tokens, int)
            or self.context_budget_tokens < 1
        ):
            raise ValueError("plan.context_budget_tokens must be a positive integer")


@dataclass(frozen=True, slots=True)
class CandidateHit:
    """A non-authoritative candidate emitted by one eligible source."""

    claim_id: str
    evidence_ids: tuple[str, ...]
    channel: CandidateChannel
    rank: int
    raw_score: float
    reason: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _required_text(self.claim_id, "candidate.claim_id"))
        object.__setattr__(self, "evidence_ids", _unique_text_tuple(self.evidence_ids, "candidate.evidence_ids"))
        channel = _required_text(self.channel, "candidate.channel")
        if channel not in _CANDIDATE_CHANNELS:
            raise ValueError(f"unsupported candidate channel: {channel}")
        object.__setattr__(self, "channel", channel)
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError("candidate.rank must be a positive integer")
        object.__setattr__(self, "raw_score", _finite_score(self.raw_score, "candidate.raw_score"))
        object.__setattr__(self, "reason", _freeze_mapping(self.reason, "candidate.reason"))


@dataclass(frozen=True, slots=True)
class RankedClaim:
    """A governed claim after fusion and deterministic ranking."""

    claim_id: str
    evidence_ids: tuple[str, ...]
    score: float
    hard_rule_tier: int
    score_components: Mapping[str, float] = field(default_factory=dict)
    explanation: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _required_text(self.claim_id, "ranked.claim_id"))
        object.__setattr__(self, "evidence_ids", _unique_text_tuple(self.evidence_ids, "ranked.evidence_ids"))
        object.__setattr__(self, "score", _finite_score(self.score, "ranked.score"))
        if isinstance(self.hard_rule_tier, bool) or not isinstance(self.hard_rule_tier, int) or self.hard_rule_tier < 0:
            raise ValueError("ranked.hard_rule_tier must be a non-negative integer")
        components = {
            _required_text(key, "ranked.score_components key"): _finite_score(value, f"ranked.score_components.{key}")
            for key, value in self.score_components.items()
        }
        object.__setattr__(self, "score_components", MappingProxyType(components))
        object.__setattr__(self, "explanation", _unique_text_tuple(self.explanation, "ranked.explanation"))


@dataclass(frozen=True, slots=True)
class RetrievalTrace:
    """Structured explanation shared by CLI, MCP, tests, and evaluation."""

    plan: RetrievalPlan
    eligible_claim_count: int
    blocked_reasons: Mapping[str, int] = field(default_factory=dict)
    source_hit_counts: Mapping[str, int] = field(default_factory=dict)
    degradation_reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.plan, RetrievalPlan):
            raise TypeError("trace.plan must be RetrievalPlan")
        if (
            isinstance(self.eligible_claim_count, bool)
            or not isinstance(self.eligible_claim_count, int)
            or self.eligible_claim_count < 0
        ):
            raise ValueError("trace.eligible_claim_count must be a non-negative integer")
        object.__setattr__(self, "blocked_reasons", self._freeze_counts(self.blocked_reasons, "blocked_reasons"))
        object.__setattr__(self, "source_hit_counts", self._freeze_counts(self.source_hit_counts, "source_hit_counts"))
        object.__setattr__(
            self,
            "degradation_reasons",
            _unique_text_tuple(self.degradation_reasons, "trace.degradation_reasons"),
        )

    @staticmethod
    def _freeze_counts(values: Mapping[str, int], field_name: str) -> Mapping[str, int]:
        normalized: dict[str, int] = {}
        for key, value in values.items():
            name = _required_text(key, f"trace.{field_name} key")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"trace.{field_name}.{name} must be a non-negative integer")
            normalized[name] = value
        return MappingProxyType(dict(sorted(normalized.items())))


__all__ = [
    "CandidateChannel",
    "CandidateHit",
    "CandidateLimits",
    "MemoryQuery",
    "Principal",
    "PrincipalKind",
    "QueryIntent",
    "RankedClaim",
    "RetrievalPlan",
    "RetrievalPurpose",
    "RetrievalTrace",
    "TimeCoordinates",
]
