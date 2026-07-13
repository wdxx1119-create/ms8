from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ms8.memory.application.legacy_migration import LegacyMigrationStagingService, prepare_legacy_migration
from ms8.memory.application.projection_service import ProjectionCoordinator, ProjectionNotReadyError
from ms8.memory.application.retrieval_context import (
    ContextAssembler,
    RetrievalEngine,
    RetrievalRequest,
    RetrievalResult,
)
from ms8.memory.domain.ledger import LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter

RECORDED_AT = "2026-07-12T08:00:00+00:00"
CONFLICT_AT = "2026-07-12T08:10:00+00:00"


def _rows() -> list[dict[str, object]]:
    return [
        {
            "id": "legacy-retrieval-1",
            "text": "User prefers concise output",
            "normalized_text": "User prefers concise output",
            "category": "user_preference",
            "status": "verified",
            "source": "ask",
            "created_at": "2026-07-01T01:02:03+00:00",
            "meta": {"confidence": 0.95, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "user_explicit",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
            "can_act_on": False,
        },
        {
            "id": "legacy-retrieval-2",
            "text": "Use concise summaries for release strategy",
            "normalized_text": "Use concise summaries for release strategy",
            "category": "product_decision",
            "status": "accepted",
            "source": "system",
            "created_at": "2026-07-02T04:05:06+00:00",
            "meta": {"confidence": 0.86, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "system_observed",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": False,
            "can_act_on": False,
        },
        {
            "id": "legacy-retrieval-3",
            "text": "Hidden concise diagnostic",
            "normalized_text": "Hidden concise diagnostic",
            "category": "system_diagnostic",
            "status": "accepted",
            "source": "system",
            "created_at": "2026-07-03T04:05:06+00:00",
            "meta": {"confidence": 0.8, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "system_observed",
            "sensitivity": "private",
            "can_recall": False,
            "can_inject": False,
            "can_act_on": False,
        },
    ]


def _append_conflict(
    store: JsonlRecordStore,
    claim_ids: tuple[str, str],
    *,
    conflict_id: str,
    recorded_at: str,
) -> None:
    verification = store.verify()
    transaction = LedgerTransaction.create(
        sequence=(verification.last_sequence or 0) + 1,
        prev_hash=verification.last_valid_hash or ("sha256:" + "0" * 64),
        actor=Actor(kind="system", id="retrieval-test"),
        recorded_at=recorded_at,
        transaction_id=f"txn_{conflict_id}",
        events=(
            LedgerEvent(
                type="conflict.detected",
                payload={
                    "conflict_id": conflict_id,
                    "claim_ids": list(claim_ids),
                    "reason": "test conflict",
                },
            ),
        ),
    )
    store.append(transaction, expected_head=verification.last_valid_hash)


def _runtime(
    tmp_path: Path,
) -> tuple[RetrievalEngine, JsonlRecordStore, ProjectionCoordinator, tuple[str, ...]]:
    prepared = prepare_legacy_migration(
        _rows(),
        migration_id="mig_retrieval_001",
        recorded_at=RECORDED_AT,
    )
    store = JsonlRecordStore(tmp_path / "ledger-v1")
    LegacyMigrationStagingService(store).apply(prepared)
    claim_ids = tuple(item.claim_id for item in prepared.plan.previews)
    _append_conflict(
        store,
        (claim_ids[0], claim_ids[1]),
        conflict_id="conflict_retrieval_001",
        recorded_at=CONFLICT_AT,
    )

    sqlite_path = tmp_path / "projections" / "memory.sqlite3"
    search_path = tmp_path / "projections" / "search.json"
    coordinator = ProjectionCoordinator(
        store,
        (
            SQLiteProjectionAdapter(sqlite_path),
            SearchProjectionAdapter(search_path),
        ),
    )
    coordinator.rebuild_all()
    engine = RetrievalEngine(
        record_store=store,
        projection_coordinator=coordinator,
        search_projection_path=search_path,
    )
    return engine, store, coordinator, claim_ids


def test_projection_backed_retrieval_filters_policy_and_marks_conflicts(tmp_path: Path) -> None:
    engine, _, _, claim_ids = _runtime(tmp_path)

    result = engine.retrieve(
        RetrievalRequest(
            text="concise output release",
            limit=10,
            realm_id="project:ms8",
            scope="project",
        )
    )

    assert result.candidate_source == "search_projection"
    assert [item.claim_id for item in result.hits] == [claim_ids[0], claim_ids[1]]
    assert claim_ids[2] not in {item.claim_id for item in result.hits}
    assert result.hits[0].score > result.hits[1].score
    assert result.hits[0].matched_terms
    assert result.hits[0].evidence_ids
    assert result.hits[0].decision_ids
    assert result.hits[0].conflict_ids == ("conflict_retrieval_001",)
    assert result.hits[0].can_recall is True
    assert result.hits[0].can_inject is True
    assert result.hits[1].can_inject is False
    assert result.policy_trace["blocked_reasons"] == {"recall_not_allowed": 1}


def test_temporal_retrieval_uses_ledger_fallback_and_valid_time_filter(tmp_path: Path) -> None:
    engine, _, _, claim_ids = _runtime(tmp_path)

    historical = engine.retrieve(
        RetrievalRequest(
            text="concise",
            recorded_as_of="2026-07-12T08:05:00+00:00",
            valid_at="2026-07-02T12:00:00+00:00",
        )
    )
    assert historical.candidate_source == "ledger_temporal_fallback"
    assert {item.claim_id for item in historical.hits} == {claim_ids[0], claim_ids[1]}
    assert all(item.conflict_ids == () for item in historical.hits)

    before_valid_time = engine.retrieve(
        RetrievalRequest(
            text="concise",
            valid_at="2026-06-01T00:00:00+00:00",
        )
    )
    assert before_valid_time.hits == ()
    assert before_valid_time.policy_trace["blocked_reasons"] == {"outside_valid_time": 3}


def test_context_assembler_rechecks_injection_and_requires_trace(tmp_path: Path) -> None:
    engine, _, _, _ = _runtime(tmp_path)
    retrieval = engine.retrieve(RetrievalRequest(text="concise release", limit=10))

    assembled = ContextAssembler(token_budget=200).assemble(retrieval)

    assert len(assembled.selected) == 1
    assert assembled.selected[0].text == "User prefers concise output"
    assert "claim:" in assembled.context
    assert "evidence:" in assembled.context
    assert "decisions:" in assembled.context
    assert len(assembled.conflict_warnings) == 1
    warning = assembled.conflict_warnings[0]
    assert warning.startswith("Conflict conflict_retrieval_001:")
    assert f"recommended={assembled.selected[0].claim_id}" in warning
    assert "candidates=" in warning
    assert "all alternatives remain retained and auditable" in warning
    assert assembled.skipped_reasons == {"can_inject_false": 1}
    assert assembled.estimated_tokens <= assembled.token_budget


def test_context_assembler_deduplicates_and_enforces_guard_and_budget(tmp_path: Path) -> None:
    engine, _, _, _ = _runtime(tmp_path)
    retrieval = engine.retrieve(RetrievalRequest(text="concise", limit=10))
    injectable = retrieval.hits[0]
    duplicate = replace(
        injectable,
        claim_id="clm_duplicate",
        evidence_ids=("evd_duplicate",),
        decision_ids=("dec_duplicate",),
        conflict_ids=(),
    )
    guarded = replace(
        injectable,
        claim_id="clm_guarded",
        text="A different injectable memory",
        evidence_ids=("evd_guarded",),
        decision_ids=("dec_guarded",),
        conflict_ids=(),
    )
    synthetic = RetrievalResult(
        query=retrieval.query,
        ledger_head=retrieval.ledger_head,
        last_sequence=retrieval.last_sequence,
        candidate_source=retrieval.candidate_source,
        hits=(injectable, duplicate, guarded),
        policy_trace=retrieval.policy_trace,
    )

    assembled = ContextAssembler(
        token_budget=200,
        injection_guard=lambda hit: hit.claim_id != "clm_guarded",
    ).assemble(synthetic)

    assert len(assembled.selected) == 1
    assert assembled.skipped_reasons == {
        "duplicate_text": 1,
        "injection_guard_denied": 1,
    }


def test_retrieval_fails_closed_when_projection_is_stale(tmp_path: Path) -> None:
    engine, store, _, claim_ids = _runtime(tmp_path)
    _append_conflict(
        store,
        (claim_ids[1], claim_ids[2]),
        conflict_id="conflict_retrieval_002",
        recorded_at="2026-07-12T08:20:00+00:00",
    )

    with pytest.raises(ProjectionNotReadyError, match="projection_stale"):
        engine.retrieve(RetrievalRequest(text="concise"))
