"""Governed candidate adapters for Hybrid Retrieval v1.

The adapters in this module deliberately depend on injected providers instead of
importing legacy search implementations.  This keeps Ledger claims authoritative:
legacy Whoosh, semantic, and graph results must first be mapped to ``claim_id`` and
accessible ``evidence_ids`` before they can participate in ranking.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

from .candidate_sources import CandidateSource, CandidateSourceError, run_candidate_source
from .eligibility import EligibleClaims
from .models import CandidateChannel, CandidateHit, RetrievalPlan

SourceStatus = Literal["healthy", "degraded"]


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


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """Provider-neutral claim candidate before a ranked ``CandidateHit`` is built."""

    claim_id: str
    evidence_ids: tuple[str, ...]
    score: float
    reason: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _required_text(self.claim_id, "record.claim_id"))
        evidence_ids = _unique_text_tuple(self.evidence_ids, "record.evidence_ids")
        if not evidence_ids:
            raise ValueError("record.evidence_ids must not be empty")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "score", float(self.score))
        normalized_reason: dict[str, Any] = {}
        for key, value in self.reason.items():
            normalized_reason[_required_text(key, "record.reason key")] = value
        object.__setattr__(self, "reason", MappingProxyType(normalized_reason))


@runtime_checkable
class ProjectionCandidateProvider(Protocol):
    """Read one projection using only an already-authorized claim whitelist."""

    def __call__(
        self,
        plan: RetrievalPlan,
        eligible_claim_ids: tuple[str, ...],
        limit: int,
    ) -> Sequence[CandidateRecord]: ...


@runtime_checkable
class LegacyCandidateProvider(Protocol):
    """Return legacy candidate objects for later authoritative claim mapping."""

    def __call__(
        self,
        plan: RetrievalPlan,
        eligible_claim_ids: tuple[str, ...],
        limit: int,
    ) -> Sequence[object]: ...


@runtime_checkable
class LegacyCandidateMapper(Protocol):
    """Map one legacy result to an evidence-backed Ledger claim candidate."""

    def __call__(self, raw_candidate: object) -> CandidateRecord: ...


class ProjectionCandidateSource:
    """Common adapter for Ledger Search/FTS and future projection providers."""

    def __init__(
        self,
        *,
        name: str,
        channel: CandidateChannel,
        provider: ProjectionCandidateProvider,
    ) -> None:
        self.name = _required_text(name, "source.name")
        self.channel = channel
        self._provider = provider

    def retrieve(self, plan: RetrievalPlan, eligible: EligibleClaims) -> tuple[CandidateHit, ...]:
        if not isinstance(plan, RetrievalPlan):
            raise TypeError("plan must be RetrievalPlan")
        if not isinstance(eligible, EligibleClaims):
            raise TypeError("eligible must be EligibleClaims")
        limit = plan.candidate_limits.for_channel(self.channel)
        records = self._provider(plan, eligible.claim_ids, limit)
        return self._build_hits(records, eligible=eligible, limit=limit, adapter_kind="projection")

    def _build_hits(
        self,
        records: Sequence[CandidateRecord],
        *,
        eligible: EligibleClaims,
        limit: int,
        adapter_kind: str,
    ) -> tuple[CandidateHit, ...]:
        normalized: list[CandidateRecord] = []
        for record in records:
            if not isinstance(record, CandidateRecord):
                raise CandidateSourceError(
                    f"candidate provider returned a non-CandidateRecord value: source={self.name}"
                )
            eligible.require(record.claim_id)
            normalized.append(record)

        ordered = sorted(normalized, key=lambda item: (-item.score, item.claim_id, item.evidence_ids))
        seen: set[str] = set()
        hits: list[CandidateHit] = []
        for record in ordered:
            if record.claim_id in seen:
                continue
            seen.add(record.claim_id)
            reason = dict(record.reason)
            reason.setdefault("source", self.name)
            reason.setdefault("adapter", adapter_kind)
            hits.append(
                CandidateHit(
                    claim_id=record.claim_id,
                    evidence_ids=record.evidence_ids,
                    channel=self.channel,
                    rank=len(hits) + 1,
                    raw_score=record.score,
                    reason=reason,
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)


class MappedLegacyCandidateSource(ProjectionCandidateSource):
    """Compatibility adapter that prevents legacy raw results becoming authority."""

    def __init__(
        self,
        *,
        name: str,
        channel: CandidateChannel,
        provider: LegacyCandidateProvider,
        mapper: LegacyCandidateMapper,
    ) -> None:
        self.name = _required_text(name, "source.name")
        self.channel = channel
        self._legacy_provider = provider
        self._mapper = mapper

    def retrieve(self, plan: RetrievalPlan, eligible: EligibleClaims) -> tuple[CandidateHit, ...]:
        if not isinstance(plan, RetrievalPlan):
            raise TypeError("plan must be RetrievalPlan")
        if not isinstance(eligible, EligibleClaims):
            raise TypeError("eligible must be EligibleClaims")
        limit = plan.candidate_limits.for_channel(self.channel)
        raw_candidates = self._legacy_provider(plan, eligible.claim_ids, limit)
        mapped: list[CandidateRecord] = []
        for raw_candidate in raw_candidates:
            record = self._mapper(raw_candidate)
            if not isinstance(record, CandidateRecord):
                raise CandidateSourceError(
                    f"legacy mapper returned a non-CandidateRecord value: source={self.name}"
                )
            mapped.append(record)
        return self._build_hits(mapped, eligible=eligible, limit=limit, adapter_kind="legacy")


class LedgerLexicalCandidateSource(ProjectionCandidateSource):
    """Adapter boundary for the current Ledger Search/FTS projection."""

    def __init__(self, provider: ProjectionCandidateProvider) -> None:
        super().__init__(name="ledger-search-fts", channel="lexical", provider=provider)


class LegacyWhooshCandidateSource(MappedLegacyCandidateSource):
    """Compatibility-only adapter for ``engine_core/whoosh_search.py``."""

    def __init__(self, provider: LegacyCandidateProvider, mapper: LegacyCandidateMapper) -> None:
        super().__init__(name="legacy-whoosh", channel="lexical", provider=provider, mapper=mapper)


class LegacySemanticCandidateSource(MappedLegacyCandidateSource):
    """Compatibility-only adapter for ``engine_core/semantic_search.py``."""

    def __init__(self, provider: LegacyCandidateProvider, mapper: LegacyCandidateMapper) -> None:
        super().__init__(name="legacy-semantic", channel="vector", provider=provider, mapper=mapper)


class LegacyGraphCandidateSource(MappedLegacyCandidateSource):
    """Compatibility-only adapter for the existing knowledge-graph candidate path."""

    def __init__(self, provider: LegacyCandidateProvider, mapper: LegacyCandidateMapper) -> None:
        super().__init__(name="legacy-graph", channel="graph", provider=provider, mapper=mapper)


@dataclass(frozen=True, slots=True)
class CandidateSourceTrace:
    source: str
    channel: CandidateChannel
    status: SourceStatus
    hit_count: int
    degradation_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _required_text(self.source, "trace.source"))
        if self.status not in {"healthy", "degraded"}:
            raise ValueError(f"unsupported source status: {self.status}")
        if isinstance(self.hit_count, bool) or not isinstance(self.hit_count, int) or self.hit_count < 0:
            raise ValueError("trace.hit_count must be a non-negative integer")
        if self.status == "healthy" and self.degradation_reason is not None:
            raise ValueError("healthy source trace cannot include a degradation reason")
        if self.status == "degraded":
            object.__setattr__(
                self,
                "degradation_reason",
                _required_text(self.degradation_reason, "trace.degradation_reason"),
            )


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    hits_by_source: Mapping[str, tuple[CandidateHit, ...]]
    traces: tuple[CandidateSourceTrace, ...]

    def __post_init__(self) -> None:
        normalized: dict[str, tuple[CandidateHit, ...]] = {}
        for source, hits in self.hits_by_source.items():
            name = _required_text(source, "batch.source")
            if not isinstance(hits, tuple) or any(not isinstance(hit, CandidateHit) for hit in hits):
                raise TypeError("batch hits must be tuples of CandidateHit")
            normalized[name] = hits
        object.__setattr__(self, "hits_by_source", MappingProxyType(dict(sorted(normalized.items()))))
        if any(not isinstance(trace, CandidateSourceTrace) for trace in self.traces):
            raise TypeError("batch traces must contain CandidateSourceTrace values")

    @property
    def degradation_reasons(self) -> tuple[str, ...]:
        return tuple(
            trace.degradation_reason
            for trace in self.traces
            if trace.degradation_reason is not None
        )


def run_candidate_sources(
    sources: Sequence[CandidateSource],
    plan: RetrievalPlan,
    eligible: EligibleClaims,
) -> CandidateBatch:
    """Run independent sources with safe degradation and contract enforcement.

    Provider/runtime failures degrade only that source. Contract and authorization
    violations remain fatal because silently accepting them could widen the
    retrieval boundary.
    """

    if not isinstance(plan, RetrievalPlan):
        raise TypeError("plan must be RetrievalPlan")
    if not isinstance(eligible, EligibleClaims):
        raise TypeError("eligible must be EligibleClaims")

    seen_names: set[str] = set()
    hits_by_source: dict[str, tuple[CandidateHit, ...]] = {}
    traces: list[CandidateSourceTrace] = []
    for source in sources:
        name = _required_text(source.name, "source.name")
        if name in seen_names:
            raise CandidateSourceError(f"candidate source names must be unique: {name}")
        seen_names.add(name)
        try:
            hits = run_candidate_source(source, plan, eligible)
        except (CandidateSourceError, PermissionError):
            raise
        except Exception as exc:  # component failure is isolated and explained
            reason = f"{name}:{type(exc).__name__}"
            hits_by_source[name] = ()
            traces.append(
                CandidateSourceTrace(
                    source=name,
                    channel=source.channel,
                    status="degraded",
                    hit_count=0,
                    degradation_reason=reason,
                )
            )
            continue
        hits_by_source[name] = hits
        traces.append(
            CandidateSourceTrace(
                source=name,
                channel=source.channel,
                status="healthy",
                hit_count=len(hits),
            )
        )
    return CandidateBatch(hits_by_source=hits_by_source, traces=tuple(traces))


__all__ = [
    "CandidateBatch",
    "CandidateRecord",
    "CandidateSourceTrace",
    "LedgerLexicalCandidateSource",
    "LegacyCandidateMapper",
    "LegacyCandidateProvider",
    "LegacyGraphCandidateSource",
    "LegacySemanticCandidateSource",
    "LegacyWhooshCandidateSource",
    "MappedLegacyCandidateSource",
    "ProjectionCandidateProvider",
    "ProjectionCandidateSource",
    "SourceStatus",
    "run_candidate_sources",
]
