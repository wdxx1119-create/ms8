from __future__ import annotations

import json

import pytest

from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, Decision, Evidence, MemoryEvent, ValidTime

FIXED_TIME = "2026-07-12T00:00:00+00:00"


def _memory_event() -> MemoryEvent:
    return MemoryEvent(
        event_id="evt_001",
        kind="document_fragment",
        content={
            "text": "Current project supports Python 3.10 through 3.13",
            "content_hash": "sha256:source",
            "tags": ["compatibility", "python"],
        },
        source={
            "system": "absorb",
            "client_id": "codex",
            "workspace_realm_id": "realm_project_alpha",
            "file": {"path_token": "docs/compatibility.md", "locator": {"start_line": 42, "end_line": 42}},
        },
        observed_at=FIXED_TIME,
        observed_at_precision="exact",
        trust_class="untrusted_document",
    )


def _claim() -> Claim:
    return Claim(
        claim_id="clm_001",
        kind="fact",
        text="Current project supports Python 3.10 through 3.13",
        subject="project:current",
        predicate="supports_python",
        value=["3.10", "3.11", "3.12", "3.13"],
        scope="project",
        realm_id="realm_project_alpha",
        authority="user_explicit",
        sensitivity="internal",
        confidence=0.98,
        status="accepted",
        valid_time=ValidTime(start="2026-07-01T00:00:00+00:00", basis="user_explicit"),
        created_from_event_id="evt_001",
    )


def test_memory_event_freezes_nested_json_values() -> None:
    event = _memory_event()

    assert event.content["tags"] == ("compatibility", "python")
    assert event.source["file"]["locator"]["start_line"] == 42
    with pytest.raises(TypeError):
        event.content["text"] = "mutated"  # type: ignore[index]


def test_claim_round_trip_preserves_temporal_and_structured_value() -> None:
    claim = _claim()

    restored = Claim.from_dict(claim.to_dict())

    assert restored == claim
    assert restored.to_dict()["value"] == ["3.10", "3.11", "3.12", "3.13"]
    assert restored.valid_time.basis == "user_explicit"


def test_valid_time_rejects_reverse_interval() -> None:
    with pytest.raises(ValueError, match="must not precede"):
        ValidTime(
            start="2026-07-12T00:00:00+00:00",
            end="2026-07-11T00:00:00+00:00",
            basis="source_metadata",
        )


def test_evidence_and_decision_round_trip() -> None:
    evidence = Evidence(
        evidence_id="evd_001",
        claim_id="clm_001",
        event_id="evt_001",
        relation="supports",
        fragment={"start_line": 42, "end_line": 42, "fragment_hash": "sha256:fragment"},
        quoted_text_hash="sha256:quote",
        weight=1.0,
    )
    decision = Decision(
        decision_id="dec_001",
        action="admit",
        result_claim_id="clm_001",
        result_status="accepted",
        policy={"engine_version": "policy-v1", "reason_codes": ["USER_EXPLICIT"], "risk_score": 0.06},
        actor=Actor(kind="user", id="sam"),
        reason="Confirmed compatibility baseline",
        recorded_at=FIXED_TIME,
    )

    assert Evidence.from_dict(evidence.to_dict()) == evidence
    assert Decision.from_dict(decision.to_dict()) == decision


def test_decision_requires_target_or_result_claim() -> None:
    with pytest.raises(ValueError, match="must target"):
        Decision(
            decision_id="dec_invalid",
            action="revoke",
            actor=Actor(kind="system", id="ms8"),
            reason="invalid test",
            recorded_at=FIXED_TIME,
        )


def test_ledger_transaction_hash_is_deterministic_and_round_trips() -> None:
    event = _memory_event()
    ledger_event = LedgerEvent(type="memory_event.recorded", payload=event.to_dict())
    transaction = LedgerTransaction.create(
        sequence=1,
        actor=Actor(kind="user", id="sam"),
        events=[ledger_event],
        prev_hash=GENESIS_HASH,
        transaction_id="txn_fixed",
        recorded_at=FIXED_TIME,
    )

    restored = LedgerTransaction.from_json_line(transaction.to_json_line())

    assert restored == transaction
    assert restored.calculate_hash() == transaction.hash
    assert json.loads(transaction.to_json_line())["hash"] == transaction.hash
    assert transaction.verify(expected_prev_hash=GENESIS_HASH, expected_sequence=1).valid is True


def test_ledger_transaction_detects_payload_tampering() -> None:
    transaction = LedgerTransaction.create(
        sequence=1,
        actor=Actor(kind="system", id="migration"),
        events=[LedgerEvent(type="claim.proposed", payload=_claim().to_dict())],
        transaction_id="txn_tamper",
        recorded_at=FIXED_TIME,
    )
    payload = transaction.to_dict()
    payload["events"][0]["payload"]["text"] = "tampered"

    with pytest.raises(ValueError, match="transaction_hash_mismatch"):
        LedgerTransaction.from_dict(payload)


def test_ledger_transaction_reports_chain_precondition_failures() -> None:
    transaction = LedgerTransaction.create(
        sequence=2,
        actor=Actor(kind="system", id="ms8"),
        events=[LedgerEvent(type="decision.made", payload={"decision_id": "dec_002"})],
        prev_hash="sha256:" + ("1" * 64),
        transaction_id="txn_second",
        recorded_at=FIXED_TIME,
    )

    verification = transaction.verify(expected_prev_hash=GENESIS_HASH, expected_sequence=1)

    assert verification.valid is False
    assert verification.reason_codes == ("previous_hash_mismatch", "sequence_mismatch")
