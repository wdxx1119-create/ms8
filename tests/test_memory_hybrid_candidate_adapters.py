from __future__ import annotations

from dataclasses import dataclass

import pytest

from ms8.memory.retrieval.adapters import (
    CandidateRecord,
    LedgerLexicalCandidateSource,
    LegacyGraphCandidateSource,
    LegacySemanticCandidateSource,
    LegacyWhooshCandidateSource,
    ProjectionCandidateSource,
    run_candidate_sources,
)
from ms8.memory.retrieval.candidate_sources import CandidateSourceError
from ms8.memory.retrieval.eligibility import EligibleClaims
from ms8.memory.retrieval.models import CandidateLimits, MemoryQuery, Principal, RetrievalPlan


def _plan(*, lexical: int = 10, vector: int = 10, graph: int = 10) -> RetrievalPlan:
    principal = Principal(
        principal_id="user:test",
        kind="user",
        realm_ids=("realm:test",),
        scopes=("project:test",),
        capabilities=("all",),
    )
    return RetrievalPlan(
        query=MemoryQuery(
            text="查找 RetrievalPlan v0.2.18",
            realm_ids=("realm:test",),
            scope="project:test",
        ),
        principal=principal,
        intent="code_symbol",
        realm_ids=("realm:test",),
        candidate_limits=CandidateLimits(
            lexical=lexical,
            vector=vector,
            entity=10,
            temporal=10,
            graph=graph,
        ),
    )


def _eligible(*claim_ids: str) -> EligibleClaims:
    return EligibleClaims(claim_ids=claim_ids, evaluated_count=len(claim_ids))


def test_projection_adapter_orders_deduplicates_and_limits() -> None:
    observed: dict[str, object] = {}

    def provider(plan: RetrievalPlan, claim_ids: tuple[str, ...], limit: int) -> list[CandidateRecord]:
        observed.update(plan=plan, claim_ids=claim_ids, limit=limit)
        return [
            CandidateRecord("claim:b", ("evidence:b",), 0.8),
            CandidateRecord("claim:a", ("evidence:a-low",), 0.2),
            CandidateRecord("claim:a", ("evidence:a-high",), 0.9),
        ]

    plan = _plan(lexical=2)
    eligible = _eligible("claim:a", "claim:b")
    source = LedgerLexicalCandidateSource(provider)

    batch = run_candidate_sources((source,), plan, eligible)

    assert observed["claim_ids"] == ("claim:a", "claim:b")
    assert observed["limit"] == 2
    hits = batch.hits_by_source["ledger-search-fts"]
    assert [hit.claim_id for hit in hits] == ["claim:a", "claim:b"]
    assert hits[0].evidence_ids == ("evidence:a-high",)
    assert [hit.rank for hit in hits] == [1, 2]
    assert hits[0].reason["adapter"] == "projection"
    assert batch.traces[0].status == "healthy"


def test_projection_adapter_fails_closed_on_ineligible_claim() -> None:
    def provider(
        _plan: RetrievalPlan,
        _claim_ids: tuple[str, ...],
        _limit: int,
    ) -> list[CandidateRecord]:
        return [CandidateRecord("claim:blocked", ("evidence:blocked",), 1.0)]

    source = LedgerLexicalCandidateSource(provider)
    with pytest.raises(PermissionError, match="outside the retrieval eligibility set"):
        run_candidate_sources((source,), _plan(), _eligible("claim:allowed"))


def test_candidate_record_requires_evidence_mapping() -> None:
    with pytest.raises(ValueError, match="evidence_ids must not be empty"):
        CandidateRecord("claim:a", (), 1.0)


@dataclass(frozen=True)
class LegacyHit:
    path: str
    claim_id: str
    evidence_id: str
    score: float


def _legacy_provider(
    _plan: RetrievalPlan,
    claim_ids: tuple[str, ...],
    _limit: int,
) -> list[object]:
    assert claim_ids == ("claim:a",)
    return [LegacyHit("MEMORY.md", "claim:a", "evidence:a", 0.7)]


def _legacy_mapper(raw: object) -> CandidateRecord:
    assert isinstance(raw, LegacyHit)
    return CandidateRecord(
        claim_id=raw.claim_id,
        evidence_ids=(raw.evidence_id,),
        score=raw.score,
        reason={"legacy_path": raw.path},
    )


@pytest.mark.parametrize(
    ("source", "expected_channel"),
    [
        (LegacyWhooshCandidateSource(_legacy_provider, _legacy_mapper), "lexical"),
        (LegacySemanticCandidateSource(_legacy_provider, _legacy_mapper), "vector"),
        (LegacyGraphCandidateSource(_legacy_provider, _legacy_mapper), "graph"),
    ],
)
def test_legacy_adapters_return_claim_hits_only(source: object, expected_channel: str) -> None:
    batch = run_candidate_sources((source,), _plan(), _eligible("claim:a"))  # type: ignore[arg-type]
    hits = next(iter(batch.hits_by_source.values()))
    assert len(hits) == 1
    assert hits[0].claim_id == "claim:a"
    assert hits[0].evidence_ids == ("evidence:a",)
    assert hits[0].channel == expected_channel
    assert hits[0].reason["adapter"] == "legacy"
    assert hits[0].reason["legacy_path"] == "MEMORY.md"


def test_runtime_failure_degrades_one_source_without_widening_eligibility() -> None:
    def broken_provider(
        _plan: RetrievalPlan,
        _claim_ids: tuple[str, ...],
        _limit: int,
    ) -> list[CandidateRecord]:
        raise OSError("projection unavailable")

    def healthy_provider(
        _plan: RetrievalPlan,
        _claim_ids: tuple[str, ...],
        _limit: int,
    ) -> list[CandidateRecord]:
        return [CandidateRecord("claim:a", ("evidence:a",), 0.5)]

    broken = ProjectionCandidateSource(name="optional-vector", channel="vector", provider=broken_provider)
    healthy = LedgerLexicalCandidateSource(healthy_provider)
    batch = run_candidate_sources((broken, healthy), _plan(), _eligible("claim:a"))

    assert batch.hits_by_source["optional-vector"] == ()
    assert batch.hits_by_source["ledger-search-fts"][0].claim_id == "claim:a"
    assert batch.degradation_reasons == ("optional-vector:OSError",)


def test_contract_violation_is_not_silently_degraded() -> None:
    class WrongChannelSource:
        name = "wrong-channel"
        channel = "lexical"

        def retrieve(self, _plan: RetrievalPlan, _eligible: EligibleClaims):
            from ms8.memory.retrieval.models import CandidateHit

            return (
                CandidateHit(
                    claim_id="claim:a",
                    evidence_ids=("evidence:a",),
                    channel="vector",
                    rank=1,
                    raw_score=1.0,
                ),
            )

    with pytest.raises(CandidateSourceError, match="candidate channel mismatch"):
        run_candidate_sources((WrongChannelSource(),), _plan(), _eligible("claim:a"))


def test_duplicate_source_names_are_rejected() -> None:
    def provider(
        _plan: RetrievalPlan,
        _claim_ids: tuple[str, ...],
        _limit: int,
    ) -> list[CandidateRecord]:
        return []

    first = ProjectionCandidateSource(name="duplicate", channel="lexical", provider=provider)
    second = ProjectionCandidateSource(name="duplicate", channel="vector", provider=provider)
    with pytest.raises(CandidateSourceError, match="names must be unique"):
        run_candidate_sources((first, second), _plan(), _eligible("claim:a"))
