from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ms8.memory.application.lifecycle import LifecycleMutationError, MemoryLifecycleService
from ms8.memory.application.lifecycle_policy import LifecyclePolicyGrant
from ms8.memory.application.projection_recovery import ProjectionRecoveryService
from ms8.memory.application.projection_service import ProjectionCoordinator
from ms8.memory.application.replay import ReplayIntegrityError, replay_transactions
from ms8.memory.compat import build_ledger_memory_compatibility_adapter
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction, canonical_json
from ms8.memory.domain.models import Actor, Claim, Decision, Evidence, MemoryEvent, ValidTime
from ms8.memory.infrastructure.fts_projection import FtsProjectionAdapter
from ms8.memory.infrastructure.graph_projection import GraphProjectionAdapter
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter
from ms8.memory.infrastructure.vector_projection import VectorProjectionAdapter
from ms8.memory.runtime_format import (
    LEDGER_V1_ENV_FLAG,
    LEDGER_V1_RUNTIME_FORMAT,
    LEGACY_RUNTIME_FORMAT,
    RUNTIME_FORMAT_SCHEMA,
    RuntimeFormatManifest,
)

RECORDED_AT = "2026-07-12T12:00:00+00:00"
MUTATED_AT = "2026-07-12T12:30:00+00:00"
CONFLICT_ID = "conf_theme_001"


def _event(event_id: str, text: str, observed_at: str) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        kind="user_input",
        content={"text": text},
        source={"system": "acceptance-test", "path": "conversation.jsonl"},
        observed_at=observed_at,
        trust_class="user_explicit",
    )


def _claim(
    claim_id: str,
    value: str,
    *,
    event_id: str,
    authority: str,
    confidence: float,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        kind="preference",
        text=f"Theme preference is {value}",
        subject="user:current",
        predicate="theme",
        value=value,
        scope="user",
        realm_id="realm_personal",
        authority=authority,
        sensitivity="internal",
        confidence=confidence,
        status="proposed",
        valid_time=ValidTime(
            start="2026-07-01T00:00:00+00:00",
            basis="user_explicit",
        ),
        created_from_event_id=event_id,
    )


def _evidence(evidence_id: str, claim_id: str, event_id: str, offset: int) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        claim_id=claim_id,
        event_id=event_id,
        relation="supports",
        fragment={
            "path": "conversation.jsonl",
            "start_offset": offset,
            "end_offset": offset + 24,
        },
        quoted_text_hash="sha256:" + ("a" if offset == 0 else "b") * 64,
    )


def _admit(decision_id: str, claim_id: str) -> Decision:
    return Decision(
        decision_id=decision_id,
        action="admit",
        result_claim_id=claim_id,
        result_status="accepted",
        policy={
            "engine_version": "acceptance-policy-v1",
            "governance": {
                "can_recall": True,
                "can_inject": True,
                "can_act_on": False,
            },
        },
        actor=Actor(kind="user", id="sam"),
        reason="accepted for stability acceptance",
        recorded_at=RECORDED_AT,
    )


def _initial_transaction() -> LedgerTransaction:
    dark_event = _event(
        "evt_theme_dark",
        "Theme preference is dark",
        "2026-07-12T10:00:00+00:00",
    )
    light_event = _event(
        "evt_theme_light",
        "Theme preference is light",
        "2026-07-12T11:00:00+00:00",
    )
    dark = _claim(
        "clm_theme_dark",
        "dark",
        event_id=dark_event.event_id,
        authority="user_implicit",
        confidence=0.70,
    )
    light = _claim(
        "clm_theme_light",
        "light",
        event_id=light_event.event_id,
        authority="user_explicit",
        confidence=0.95,
    )
    return LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_theme_conflict",
        recorded_at=RECORDED_AT,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=dark_event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=dark.to_dict()),
            LedgerEvent(
                type="evidence.linked",
                payload=_evidence("evd_theme_dark", dark.claim_id, dark_event.event_id, 0).to_dict(),
            ),
            LedgerEvent(type="decision.made", payload=_admit("dec_theme_dark", dark.claim_id).to_dict()),
            LedgerEvent(type="memory_event.recorded", payload=light_event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=light.to_dict()),
            LedgerEvent(
                type="evidence.linked",
                payload=_evidence("evd_theme_light", light.claim_id, light_event.event_id, 30).to_dict(),
            ),
            LedgerEvent(type="decision.made", payload=_admit("dec_theme_light", light.claim_id).to_dict()),
            LedgerEvent(
                type="conflict.detected",
                payload={
                    "conflict_id": CONFLICT_ID,
                    "realm_id": "realm_personal",
                    "subject": "user:current",
                    "predicate": "theme",
                    "claim_ids": [dark.claim_id, light.claim_id],
                    "reason": "overlapping valid time with incompatible values",
                    "detected_at": RECORDED_AT,
                },
            ),
        ),
    )


def _runtime(tmp_path: Path) -> dict[str, Any]:
    workspace = tmp_path / "workspace"
    projection_root = workspace / "memory" / "projections"
    ledger_root = workspace / "memory" / "ledger-v1"
    manifest_path = workspace / "memory" / "runtime-format.json"
    paths = {
        "sqlite": projection_root / "memory.sqlite3",
        "search": projection_root / "search.json",
        "fts": projection_root / "fts.json",
        "vector": projection_root / "vector.json",
        "graph": projection_root / "graph.json",
    }
    store = JsonlRecordStore(ledger_root)
    transaction = _initial_transaction()
    store.append(transaction, expected_head=GENESIS_HASH)
    adapters = (
        SQLiteProjectionAdapter(paths["sqlite"]),
        SearchProjectionAdapter(paths["search"]),
        FtsProjectionAdapter(paths["fts"]),
        VectorProjectionAdapter(paths["vector"]),
        GraphProjectionAdapter(paths["graph"]),
    )
    coordinator = ProjectionCoordinator(store, adapters)
    build = coordinator.rebuild_all()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = RuntimeFormatManifest(
        schema=RUNTIME_FORMAT_SCHEMA,
        active_format=LEDGER_V1_RUNTIME_FORMAT,
        generation=1,
        updated_at=RECORDED_AT,
        previous_format=LEGACY_RUNTIME_FORMAT,
        migration_id="mig_acceptance_001",
        ledger_head=build.ledger_head,
    )
    manifest_path.write_text(canonical_json(manifest.to_dict()) + "\n", encoding="utf-8")
    adapter = build_ledger_memory_compatibility_adapter(
        {"memory_ledger_v1": {"enabled": True}},
        workspace,
        environ={LEDGER_V1_ENV_FLAG: "1"},
    )
    assert adapter is not None
    return {
        "workspace": workspace,
        "store": store,
        "coordinator": coordinator,
        "adapter": adapter,
        "paths": paths,
        "transaction": transaction,
    }


def test_query_explain_and_context_return_complete_conflict_details(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    adapter = runtime["adapter"]

    query = adapter.query("theme preference", 10)

    assert query["count"] == 2
    for row in query["results"]:
        conflict = row["conflicts"][0]
        assert conflict["conflict_id"] == CONFLICT_ID
        assert conflict["reason"] == "overlapping valid time with incompatible values"
        assert conflict["recommended_claim_id"] == "clm_theme_light"
        assert {item["claim_id"] for item in conflict["candidates"]} == {
            "clm_theme_dark",
            "clm_theme_light",
        }
        assert conflict["recommendation_explanation"][-1] == (
            "all alternatives remain retained and auditable"
        )

    context = adapter.context("theme preference", 10)
    warnings = context["context"]["conflict_warnings"]
    assert len(warnings) == 1
    assert CONFLICT_ID in warnings[0]
    assert "recommended=clm_theme_light" in warnings[0]
    assert "candidates=clm_theme_light,clm_theme_dark" in warnings[0]

    explained = adapter.explain("clm_theme_dark")
    assert explained["conflicts"][0]["recommended_claim_id"] == "clm_theme_light"
    assert len(explained["conflicts"][0]["candidates"]) == 2


def test_recorded_observed_and_valid_time_are_independent(tmp_path: Path) -> None:
    adapter = _runtime(tmp_path)["adapter"]

    observed = adapter.query(
        "theme preference",
        10,
        observed_as_of="2026-07-12T10:30:00+00:00",
    )
    assert [row["id"] for row in observed["results"]] == ["clm_theme_dark"]
    assert observed["retrieval_gateway"]["policy_filter"]["blocked_reasons"] == {
        "observed_after_cutoff": 1
    }

    not_recorded = adapter.query(
        "theme preference",
        10,
        recorded_as_of="2026-07-12T11:59:59+00:00",
    )
    assert not_recorded["count"] == 0
    assert not_recorded["ledger_v1"]["candidate_source"] == "ledger_temporal_fallback"

    not_valid = adapter.query(
        "theme preference",
        10,
        valid_at="2026-06-30T23:59:59+00:00",
    )
    assert not_valid["count"] == 0
    assert not_valid["retrieval_gateway"]["policy_filter"]["blocked_reasons"] == {
        "outside_valid_time": 2
    }

    time_coordinates = observed["retrieval_gateway"]["policy_filter"]["time_coordinates"]
    assert time_coordinates == {
        "recorded_as_of": None,
        "observed_as_of": "2026-07-12T10:30:00+00:00",
        "valid_at": None,
    }


def test_sqlite_fts_vector_graph_and_search_rebuild_from_ledger(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    store = runtime["store"]
    coordinator = runtime["coordinator"]
    adapter = runtime["adapter"]
    paths = runtime["paths"]
    expected_head = str(store.verify().last_valid_hash)
    before = adapter.query("theme preference", 10)

    for path in paths.values():
        path.unlink()
    assert coordinator.status().ready_for_query is False

    service = ProjectionRecoveryService(coordinator)
    preview = service.rebuild(expected_head)
    assert preview.applied is False
    assert not any(path.exists() for path in paths.values())

    rebuilt = service.rebuild(
        expected_head,
        apply=True,
        confirmation=service.confirmation_token(expected_head),
    )
    assert rebuilt.applied is True
    assert rebuilt.ready_after is True
    assert rebuilt.rebuilt_projections == ("sqlite", "search", "fts", "vector", "graph")
    assert all(path.is_file() for path in paths.values())
    status = coordinator.require_ready_for_query()
    assert status.ledger_head == expected_head
    assert {item.name for item in status.freshness} == {
        "sqlite",
        "search",
        "fts",
        "vector",
        "graph",
    }
    assert all(item.logical_state_hash == status.logical_state_hash for item in status.freshness)

    after = adapter.query("theme preference", 10)
    assert [row["id"] for row in before["results"]] == [row["id"] for row in after["results"]]
    assert before["results"][0]["conflicts"] == after["results"][0]["conflicts"]


def _grant(
    *,
    actor: Actor,
    grant_id: str,
    actions: tuple[str, ...] = ("revoke",),
    claims: tuple[str, ...] = ("clm_theme_dark",),
    issued_at: str = "2026-07-12T12:00:00+00:00",
    expires_at: str = "2026-07-12T13:00:00+00:00",
) -> LifecyclePolicyGrant:
    return LifecyclePolicyGrant.create(
        grant_id=grant_id,
        policy_engine="ms8-policy-core",
        policy_version="acceptance-v1",
        actor=actor,
        allowed_actions=actions,
        target_claim_ids=claims,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=f"nonce-{grant_id}",
    )


def test_automated_lifecycle_requires_verified_policyengine_grant(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    store = runtime["store"]
    head = str(store.verify().last_valid_hash)
    actor = Actor(kind="system", id="maintenance-agent")
    valid = _grant(actor=actor, grant_id="grant-valid")

    with pytest.raises(LifecycleMutationError, match="configured PolicyEngine verifier"):
        MemoryLifecycleService(store).revoke(
            target_claim_id="clm_theme_dark",
            actor=actor,
            reason="automated cleanup",
            recorded_at=MUTATED_AT,
            expected_head_hash=head,
            policy={"policy_authorization": valid.to_dict()},
        )
    assert store.verify().transaction_count == 1

    guarded = MemoryLifecycleService(
        store,
        policy_verifier=lambda grant: grant.grant_id == "grant-valid",
    )
    with pytest.raises(LifecycleMutationError, match="requires a PolicyEngine grant"):
        guarded.revoke(
            target_claim_id="clm_theme_dark",
            actor=actor,
            reason="missing authorization",
            recorded_at=MUTATED_AT,
            expected_head_hash=head,
        )

    wrong_action = _grant(
        actor=actor,
        grant_id="grant-wrong-action",
        actions=("expire",),
    )
    with pytest.raises(LifecycleMutationError, match="does not authorize this action"):
        guarded.revoke(
            target_claim_id="clm_theme_dark",
            actor=actor,
            reason="wrong action",
            recorded_at=MUTATED_AT,
            expected_head_hash=head,
            policy={"policy_authorization": wrong_action.to_dict()},
        )

    wrong_claim = _grant(
        actor=actor,
        grant_id="grant-wrong-claim",
        claims=("clm_theme_light",),
    )
    with pytest.raises(LifecycleMutationError, match="does not cover all target claims"):
        guarded.revoke(
            target_claim_id="clm_theme_dark",
            actor=actor,
            reason="wrong claim",
            recorded_at=MUTATED_AT,
            expected_head_hash=head,
            policy={"policy_authorization": wrong_claim.to_dict()},
        )

    expired = _grant(
        actor=actor,
        grant_id="grant-expired",
        expires_at="2026-07-12T12:20:00+00:00",
    )
    with pytest.raises(LifecycleMutationError, match="has expired"):
        guarded.revoke(
            target_claim_id="clm_theme_dark",
            actor=actor,
            reason="expired grant",
            recorded_at=MUTATED_AT,
            expected_head_hash=head,
            policy={"policy_authorization": expired.to_dict()},
        )

    result = guarded.revoke(
        target_claim_id="clm_theme_dark",
        actor=actor,
        reason="verified automated cleanup",
        recorded_at=MUTATED_AT,
        expected_head_hash=head,
        policy={"policy_authorization": valid.to_dict()},
        decision_id="dec_automated_revoke",
        transaction_id="txn_automated_revoke",
    )
    assert result.previous_head == head
    state = replay_transactions(store.iterate())
    assert state.claims["clm_theme_dark"].current_status == "revoked"
    assert (
        state.decisions["dec_automated_revoke"].to_dict()["policy"]["policy_authorization"]
        == valid.to_dict()
    )


def test_injectable_replacement_without_new_evidence_is_rejected_before_append(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    store = runtime["store"]
    head = str(store.verify().last_valid_hash)
    actor = Actor(kind="system", id="maintenance-agent")
    grant = _grant(
        actor=actor,
        grant_id="grant-correct",
        actions=("correct",),
    )
    service = MemoryLifecycleService(store, policy_verifier=lambda candidate: candidate == grant)
    replacement = _claim(
        "clm_theme_dark_corrected",
        "midnight",
        event_id="evt_theme_dark",
        authority="user_explicit",
        confidence=0.99,
    )

    with pytest.raises(ReplayIntegrityError, match="requires at least one evidence"):
        service.correct(
            target_claim_id="clm_theme_dark",
            replacement=replacement,
            actor=actor,
            reason="automated correction without evidence",
            recorded_at=MUTATED_AT,
            expected_head_hash=head,
            policy={
                "policy_authorization": grant.to_dict(),
                "governance": {
                    "can_recall": True,
                    "can_inject": True,
                    "can_act_on": False,
                },
            },
            decision_id="dec_invalid_correct",
            transaction_id="txn_invalid_correct",
        )

    assert store.verify().transaction_count == 1
    assert "clm_theme_dark_corrected" not in replay_transactions(store.iterate()).claims
