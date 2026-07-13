from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ms8.memory_safety import evaluate_memory_policy, pre_action_check
from ms8.record_policy import (
    append_canonical_record,
    build_canonical_record,
    repair_scope_flags,
    validate_file_and_quarantine,
    validate_record,
)


def _legacy_record_without_provenance() -> dict[str, Any]:
    return {
        "id": "legacy-1",
        "text": "Legacy memory",
        "normalized_text": "Legacy memory",
        "category": "general",
        "status": "accepted",
        "source": "legacy",
        "created_at": "2026-07-12T00:00:00+00:00",
        "meta": {"admission": "legacy"},
        "scope": "personal",
        "authority": "user_implicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
        "unknown_extension": {"keep": True},
    }


def test_canonical_record_normalization_categories_and_governance() -> None:
    preference = build_canonical_record("  我喜欢\n  简洁输出  ", "ask")
    assert preference["text"] == "我喜欢 简洁输出"
    assert preference["normalized_text"] == "我喜欢 简洁输出"
    assert preference["category"] == "user_preference"
    assert preference["scope"] == "personal"
    assert preference["authority"] == "user_explicit"
    assert preference["sensitivity"] == "private"
    assert preference["can_recall"] is True
    assert preference["can_inject"] is True
    assert preference["can_act_on"] is False
    assert validate_record(preference) == (True, "ok")

    product_decision = build_canonical_record("发布策略采用分阶段路线", "system")
    assert product_decision["category"] == "product_decision"
    assert product_decision["scope"] == "project"
    assert product_decision["authority"] == "system_observed"
    assert product_decision["can_inject"] is True

    diagnostic = build_canonical_record("doctor traceback detected", "system")
    assert diagnostic["category"] == "system_diagnostic"
    assert diagnostic["scope"] == "system_debug"
    assert diagnostic["can_recall"] is True
    assert diagnostic["can_inject"] is False
    assert diagnostic["can_act_on"] is False

    experiment = build_canonical_record("candidate observation", "labs:trial")
    assert experiment["category"] == "experimental_note"
    assert experiment["scope"] == "labs"
    assert experiment["can_inject"] is False

    general = build_canonical_record("Ordinary factual note", "ask")
    assert general["category"] == "general"
    assert general["scope"] == "personal"


def test_invalid_status_falls_back_and_legacy_without_provenance_remains_valid() -> None:
    fallback = build_canonical_record("Unknown status", "ask", status="not-a-status")
    assert fallback["status"] == "candidate"
    assert validate_record(fallback) == (True, "ok")

    legacy = _legacy_record_without_provenance()
    assert "provenance" not in legacy
    assert validate_record(legacy) == (True, "ok")


def test_provenance_digest_tampering_is_rejected() -> None:
    record = build_canonical_record("Original text", "ask", status="verified")
    tampered = json.loads(json.dumps(record))
    tampered["text"] = "Tampered text"
    tampered["normalized_text"] = "Tampered text"

    ok, reason = validate_record(tampered)
    assert ok is False
    assert reason == "invalid:provenance_content_digest"


def test_append_and_quarantine_preserve_valid_records(tmp_path: Path) -> None:
    records_file = tmp_path / "records.jsonl"
    quarantine_file = tmp_path / "quarantine.jsonl"

    appended, ok, reason = append_canonical_record(
        records_file=records_file,
        quarantine_file=quarantine_file,
        text="  stable\n memory  ",
        source="ask",
    )
    assert ok is True
    assert reason == "ok"
    assert appended["normalized_text"] == "stable memory"
    assert quarantine_file.exists() is False

    valid = build_canonical_record("Valid memory", "ask")
    invalid = json.loads(json.dumps(valid))
    invalid["normalized_text"] = "Digest mismatch"
    records_file.write_text(
        "\n".join((json.dumps(valid, ensure_ascii=False), json.dumps(invalid, ensure_ascii=False))) + "\n",
        encoding="utf-8",
    )

    validate_file_and_quarantine(records_file, quarantine_file)

    retained = [json.loads(line) for line in records_file.read_text(encoding="utf-8").splitlines()]
    quarantined = [json.loads(line) for line in quarantine_file.read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in retained] == [valid["id"]]
    assert len(quarantined) == 1
    assert quarantined[0]["reason"] == "invalid:provenance_content_digest"


def test_governance_and_provenance_repair_is_additive_idempotent_and_dry_run_safe(tmp_path: Path) -> None:
    records_file = tmp_path / "records.jsonl"
    legacy = {
        "id": "legacy-repair-1",
        "text": "我喜欢保留未知字段",
        "normalized_text": "我喜欢保留未知字段",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "created_at": "2026-07-12T00:00:00+00:00",
        "meta": {},
        "unknown_extension": {"nested": [1, 2, 3]},
    }
    original = json.dumps(legacy, ensure_ascii=False) + "\n"
    records_file.write_text(original, encoding="utf-8")

    dry_run = repair_scope_flags(records_file, dry_run=True)
    assert dry_run["mode"] == "dry_run"
    assert dry_run["updated"] == 1
    assert records_file.read_text(encoding="utf-8") == original

    applied = repair_scope_flags(records_file)
    assert applied["mode"] == "apply"
    assert applied["updated"] == 1
    assert applied["provenance_backfilled"] == 1

    repaired = json.loads(records_file.read_text(encoding="utf-8"))
    assert repaired["unknown_extension"] == {"nested": [1, 2, 3]}
    assert repaired["category"] == "user_preference"
    assert repaired["scope"] == "personal"
    assert repaired["authority"] == "user_explicit"
    assert repaired["can_act_on"] is False
    assert validate_record(repaired) == (True, "ok")

    second = repair_scope_flags(records_file)
    assert second["updated"] == 0
    assert second["provenance_backfilled"] == 0
    assert json.loads(records_file.read_text(encoding="utf-8")) == repaired


def test_policy_reports_low_confidence_without_leaking_execution_authority() -> None:
    record = build_canonical_record("Low-confidence note", "ask", status="verified")
    record["provenance"]["confidence"] = 0.2

    decision = evaluate_memory_policy(record, query="note", purpose="recall")
    assert decision["allowed"] is False
    assert "low_confidence" in decision["reason_codes"]
    assert record["can_act_on"] is False


def test_pre_action_check_is_advisory_exact_scope_and_confirmation_gated() -> None:
    record = build_canonical_record("Deployment authorization", "ask", status="verified")
    record["can_act_on"] = True
    record["authorized_action"] = "deploy release 0.2.17"

    no_support = pre_action_check(action="deploy release 0.2.17", records=[record])
    assert no_support["decision"] == "deny"
    assert no_support["execution_performed"] is False
    assert no_support["reason_counts"] == {"supporting_memory_required": 1}

    no_confirmation = pre_action_check(
        action="deploy release 0.2.17",
        records=[record],
        memory_ids=[record["id"]],
    )
    assert no_confirmation["decision"] == "deny"
    assert no_confirmation["requires_confirmation"] is True
    assert no_confirmation["reason_counts"] == {"human_confirmation_required": 1}

    allowed = pre_action_check(
        action="deploy release 0.2.17",
        records=[record],
        memory_ids=[record["id"]],
        explicit_user_confirmation=True,
    )
    assert allowed["decision"] == "allow"
    assert allowed["allowed"] is True
    assert allowed["execution_performed"] is False
    assert allowed["eligible_record_ids"] == [record["id"]]

    wrong_scope = pre_action_check(
        action="delete production data",
        records=[record],
        memory_ids=[record["id"]],
        explicit_user_confirmation=True,
    )
    assert wrong_scope["decision"] == "deny"
    assert wrong_scope["execution_performed"] is False
    assert wrong_scope["reason_counts"]["action_scope_mismatch"] == 1
