from __future__ import annotations

import json
from pathlib import Path

from ms8.memory.infrastructure.search_projection import SEARCH_PROJECTION_SCHEMA
from ms8.memory.retrieval.adapters import run_candidate_sources
from ms8.memory.retrieval.entity_sources import (
    EntityProjectionCandidateProvider,
    EntityProjectionCandidateSource,
    normalize_entity_label,
)
from ms8.memory.retrieval.eligibility import EligibleClaims
from ms8.memory.retrieval.models import MemoryQuery, Principal, RetrievalPlan


def _plan(*, text: str, entity_mentions: tuple[str, ...] = ()) -> RetrievalPlan:
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
        intent="open_recall",
        realm_ids=("realm:test",),
        entity_mentions=entity_mentions,
    )


def _write_projection(path: Path, *, schema: str = SEARCH_PROJECTION_SCHEMA) -> None:
    path.write_text(
        json.dumps(
            {
                "manifest": {"schema": schema},
                "documents": [
                    {
                        "claim_id": "claim:allowed",
                        "subject": "MS8",
                        "aliases": ["Hybrid Retrieval", "记忆检索"],
                    },
                    {
                        "claim_id": "claim:blocked",
                        "subject": "Secret Project",
                        "aliases": ["Hybrid Retrieval"],
                    },
                ],
                "postings": {},
            }
        ),
        encoding="utf-8",
    )


def test_entity_alias_match_only_resolves_eligible_claims(tmp_path: Path) -> None:
    path = tmp_path / "search.json"
    _write_projection(path)
    resolved: list[str] = []

    def resolve_evidence(claim_id: str) -> tuple[str, ...]:
        resolved.append(claim_id)
        return (f"evidence:{claim_id}",)

    source = EntityProjectionCandidateSource(
        EntityProjectionCandidateProvider(path, resolve_evidence)
    )
    eligible = EligibleClaims(claim_ids=("claim:allowed",), evaluated_count=2)

    batch = run_candidate_sources(
        (source,),
        _plan(text="Explain hybrid retrieval", entity_mentions=("Hybrid Retrieval",)),
        eligible,
    )

    hits = batch.hits_by_source["entity-projection"]
    assert [item.claim_id for item in hits] == ["claim:allowed"]
    assert resolved == ["claim:allowed"]
    assert hits[0].reason == {
        "projection_schema": SEARCH_PROJECTION_SCHEMA,
        "entity_field": "alias",
        "matched_entity": "hybrid retrieval",
        "match_kind": "exact",
        "source": "entity-projection",
        "adapter": "projection",
    }


def test_entity_subject_match_is_deterministic_and_preferred(tmp_path: Path) -> None:
    path = tmp_path / "search.json"
    _write_projection(path)
    provider = EntityProjectionCandidateProvider(
        path,
        lambda claim_id: (f"evidence:{claim_id}",),
    )

    records = provider(_plan(text="What is MS8?"), ("claim:allowed",), 5)

    assert len(records) == 1
    assert records[0].claim_id == "claim:allowed"
    assert records[0].score == 1.0
    assert records[0].reason["entity_field"] == "subject"
    assert records[0].reason["matched_entity"] == "ms8"


def test_entity_candidate_without_accessible_evidence_is_excluded(tmp_path: Path) -> None:
    path = tmp_path / "search.json"
    _write_projection(path)
    provider = EntityProjectionCandidateProvider(path, lambda _claim_id: ())

    records = provider(
        _plan(text="记忆检索", entity_mentions=("记忆检索",)),
        ("claim:allowed",),
        5,
    )

    assert records == ()


def test_invalid_entity_projection_degrades_inside_same_eligibility(tmp_path: Path) -> None:
    path = tmp_path / "search.json"
    _write_projection(path, schema="invalid")
    source = EntityProjectionCandidateSource(
        EntityProjectionCandidateProvider(path, lambda claim_id: (f"evidence:{claim_id}",))
    )
    eligible = EligibleClaims(claim_ids=("claim:allowed",), evaluated_count=1)

    batch = run_candidate_sources((source,), _plan(text="MS8"), eligible)

    assert batch.hits_by_source["entity-projection"] == ()
    assert batch.degradation_reasons == (
        "entity-projection:EntityProjectionFormatError",
    )


def test_entity_normalization_is_nfkc_casefolded_and_space_stable() -> None:
    assert normalize_entity_label("  ＭＳ８   Retrieval  ") == "ms8 retrieval"
