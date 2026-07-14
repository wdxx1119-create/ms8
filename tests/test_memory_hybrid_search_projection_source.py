from __future__ import annotations

import json
from pathlib import Path

from ms8.memory.infrastructure.search_projection import SEARCH_PROJECTION_SCHEMA
from ms8.memory.retrieval.adapters import LedgerLexicalCandidateSource, run_candidate_sources
from ms8.memory.retrieval.eligibility import EligibleClaims
from ms8.memory.retrieval.models import MemoryQuery, Principal, RetrievalPlan
from ms8.memory.retrieval.projection_sources import SearchProjectionCandidateProvider


def _plan(text: str = "RetrievalPlan") -> RetrievalPlan:
    principal = Principal(
        principal_id="user:test",
        kind="user",
        realm_ids=("realm:test",),
        scopes=("project:test",),
        capabilities=("all",),
    )
    return RetrievalPlan(
        query=MemoryQuery(
            text=text,
            realm_ids=("realm:test",),
            scope="project:test",
        ),
        principal=principal,
        intent="code_symbol",
        realm_ids=("realm:test",),
    )


def _write_projection(path: Path, *, schema: str = SEARCH_PROJECTION_SCHEMA) -> None:
    payload = {
        "manifest": {"schema": schema},
        "documents": [
            {
                "claim_id": "claim:allowed",
                "text": "RetrievalPlan keeps policy coordinates explicit",
                "terms": ["retrievalplan", "retrieval", "plan", "policy"],
            },
            {
                "claim_id": "claim:blocked",
                "text": "RetrievalPlan from another realm",
                "terms": ["retrievalplan", "retrieval", "plan"],
            },
        ],
        "postings": {
            "retrievalplan": ["claim:allowed", "claim:blocked"],
            "retrieval": ["claim:allowed", "claim:blocked"],
            "plan": ["claim:allowed", "claim:blocked"],
            "policy": ["claim:allowed"],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_search_projection_filters_before_candidate_mapping(tmp_path: Path) -> None:
    projection_path = tmp_path / "search.json"
    _write_projection(projection_path)
    resolved: list[str] = []

    def resolve_evidence(claim_id: str) -> tuple[str, ...]:
        resolved.append(claim_id)
        return (f"evidence:{claim_id}",)

    provider = SearchProjectionCandidateProvider(projection_path, resolve_evidence)
    source = LedgerLexicalCandidateSource(provider)
    eligible = EligibleClaims(claim_ids=("claim:allowed",), evaluated_count=2)

    batch = run_candidate_sources((source,), _plan(), eligible)

    hits = batch.hits_by_source["ledger-search-fts"]
    assert [hit.claim_id for hit in hits] == ["claim:allowed"]
    assert resolved == ["claim:allowed"]
    assert hits[0].evidence_ids == ("evidence:claim:allowed",)
    assert hits[0].reason["projection_schema"] == SEARCH_PROJECTION_SCHEMA
    assert "retrievalplan" in hits[0].reason["matched_terms"]


def test_search_projection_rejects_single_generic_match_in_broad_question(tmp_path: Path) -> None:
    projection_path = tmp_path / "search.json"
    payload = {
        "manifest": {"schema": SEARCH_PROJECTION_SCHEMA},
        "documents": [
            {
                "claim_id": "claim:retention",
                "text": "Backup retention is 30 days",
                "terms": ["backup", "retention", "day"],
            },
            {
                "claim_id": "claim:release",
                "text": "Current release policy requires all checks",
                "terms": ["current", "release", "policy", "check"],
            },
        ],
        "postings": {
            "backup": ["claim:retention"],
            "retention": ["claim:retention"],
            "policy": ["claim:release"],
        },
    }
    projection_path.write_text(json.dumps(payload), encoding="utf-8")
    provider = SearchProjectionCandidateProvider(
        projection_path,
        lambda claim_id: (f"evidence:{claim_id}",),
    )
    source = LedgerLexicalCandidateSource(provider)
    eligible = EligibleClaims(
        claim_ids=("claim:release", "claim:retention"),
        evaluated_count=2,
    )

    batch = run_candidate_sources(
        (source,),
        _plan("What is the backup retention policy?"),
        eligible,
    )

    hits = batch.hits_by_source["ledger-search-fts"]
    assert [hit.claim_id for hit in hits] == ["claim:retention"]
    assert hits[0].reason["matched_terms"] == ("backup", "retention")
    assert hits[0].reason["informative_query_term_count"] == 3


def test_search_projection_skips_claim_without_accessible_evidence(tmp_path: Path) -> None:
    projection_path = tmp_path / "search.json"
    _write_projection(projection_path)
    provider = SearchProjectionCandidateProvider(projection_path, lambda _claim_id: ())
    source = LedgerLexicalCandidateSource(provider)
    eligible = EligibleClaims(claim_ids=("claim:allowed",), evaluated_count=2)

    batch = run_candidate_sources((source,), _plan(), eligible)

    assert batch.hits_by_source["ledger-search-fts"] == ()
    assert batch.traces[0].status == "healthy"


def test_invalid_search_projection_degrades_without_unfiltered_fallback(tmp_path: Path) -> None:
    projection_path = tmp_path / "search.json"
    _write_projection(projection_path, schema="invalid.schema")
    provider = SearchProjectionCandidateProvider(
        projection_path,
        lambda claim_id: (f"evidence:{claim_id}",),
    )
    source = LedgerLexicalCandidateSource(provider)
    eligible = EligibleClaims(claim_ids=("claim:allowed",), evaluated_count=2)

    batch = run_candidate_sources((source,), _plan(), eligible)

    assert batch.hits_by_source["ledger-search-fts"] == ()
    assert batch.degradation_reasons == ("ledger-search-fts:SearchProjectionFormatError",)
