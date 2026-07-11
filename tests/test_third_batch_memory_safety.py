from __future__ import annotations

import json

from ms8.engine import MemoryCoreEngine
from ms8.memory_safety import evaluate_memory_policy, pre_action_check, validate_memory_provenance
from ms8.record_policy import build_canonical_record, repair_scope_flags, validate_record


def test_new_canonical_record_contains_valid_provenance() -> None:
    row = build_canonical_record("User explicitly chose local storage", "ask")
    ok, reason = validate_record(row)
    assert ok, reason
    provenance_ok, provenance_reason = validate_memory_provenance(row, row["provenance"])
    assert provenance_ok, provenance_reason
    assert row["provenance"]["source_kind"] == "user"
    assert row["provenance"]["verification_state"] == "user_asserted"
    assert row["provenance"]["confidence"] >= 0.9


def test_old_record_remains_valid_and_backfill_is_idempotent(tmp_path) -> None:
    path = tmp_path / "records.jsonl"
    row = build_canonical_record("Keep this record", "ask")
    row.pop("provenance")
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    ok, reason = validate_record(row)
    assert ok, reason

    dry = repair_scope_flags(path, dry_run=True)
    assert dry["provenance_backfilled"] == 1
    assert "provenance" not in json.loads(path.read_text(encoding="utf-8"))

    applied = repair_scope_flags(path)
    assert applied["provenance_backfilled"] == 1
    first = json.loads(path.read_text(encoding="utf-8"))
    assert first["provenance"]["content_sha256"]
    repeated = repair_scope_flags(path)
    assert repeated["provenance_backfilled"] == 0


def test_low_confidence_memory_has_explainable_refusal() -> None:
    row = build_canonical_record("Assistant guessed a preference", "labs:assistant")
    row["scope"] = "personal"
    row["status"] = "accepted"
    row["authority"] = "assistant_inferred"
    row["provenance"]["confidence"] = 0.2
    decision = evaluate_memory_policy(row, query="preference", purpose="recall")
    assert decision["allowed"] is False
    assert "unverified_low_authority" in decision["reason_codes"]
    assert "low_confidence" in decision["reason_codes"]


def test_pre_action_requires_independent_authority_and_confirmation() -> None:
    ordinary = build_canonical_record("User prefers local storage", "ask")
    denied = pre_action_check(
        action="delete remote copy",
        records=[ordinary],
        memory_ids=[ordinary["id"]],
        explicit_user_confirmation=True,
    )
    assert denied["allowed"] is False
    assert denied["reason_counts"]["action_not_authorized_by_record"] == 1

    authorized = build_canonical_record(
        "User explicitly authorizes this exact cleanup",
        "ask",
        status="verified",
    )
    authorized["can_act_on"] = True
    authorized["authorized_action"] = "delete remote copy"
    authorized["provenance"]["verification_state"] = "verified"
    authorized["provenance"]["confidence"] = 1.0
    no_confirmation = pre_action_check(
        action="delete remote copy",
        records=[authorized],
        memory_ids=[authorized["id"]],
    )
    assert no_confirmation["allowed"] is False
    assert no_confirmation["requires_confirmation"] is True
    allowed = pre_action_check(
        action="delete remote copy",
        records=[authorized],
        memory_ids=[authorized["id"]],
        explicit_user_confirmation=True,
    )
    assert allowed["allowed"] is True
    assert allowed["execution_performed"] is False


def test_pre_action_fails_closed_without_explicit_or_uniform_support() -> None:
    authorized = build_canonical_record("Exact action authorization", "ask", status="verified")
    authorized["can_act_on"] = True
    authorized["authorized_action"] = "delete remote copy"
    authorized["provenance"]["verification_state"] = "verified"
    authorized["provenance"]["confidence"] = 1.0

    without_support = pre_action_check(
        action="delete remote copy",
        records=[authorized],
        explicit_user_confirmation=True,
    )
    assert without_support["allowed"] is False
    assert without_support["reason_counts"]["supporting_memory_required"] == 1
    assert without_support["evaluated_record_ids"] == []

    mismatched = pre_action_check(
        action="send an email",
        records=[authorized],
        memory_ids=[authorized["id"]],
        explicit_user_confirmation=True,
    )
    assert mismatched["allowed"] is False
    assert mismatched["reason_counts"]["action_scope_mismatch"] == 1

    untrusted = build_canonical_record("Assistant suggested the same action", "mcp:assistant")
    mixed = pre_action_check(
        action="delete remote copy",
        records=[authorized, untrusted],
        memory_ids=[authorized["id"], untrusted["id"]],
        explicit_user_confirmation=True,
    )
    assert mixed["allowed"] is False
    assert authorized["id"] in mixed["eligible_record_ids"]
    assert mixed["reason_counts"]["action_not_authorized_by_record"] == 1


def test_engine_trace_counts_policy_reasons() -> None:
    engine = MemoryCoreEngine.__new__(MemoryCoreEngine)
    row = build_canonical_record("Low confidence tool claim", "mcp:tool")
    row["authority"] = "tool_generated"
    row["provenance"]["confidence"] = 0.1
    allowed, trace = engine._filter_rows_by_policy([row], query="claim", purpose="recall", limit=10)
    assert allowed == []
    assert trace["blocked_total"] == 1
    assert trace["reason_counts"]["low_confidence"] == 1
    assert trace["low_confidence_refusal"] == 1
