from __future__ import annotations

from ms8.connect.mcp_server.memory_access_policy import memory_row_browsable
from ms8.memory_safety import evaluate_memory_policy
from ms8.record_policy import build_canonical_record


def test_explicit_browse_does_not_apply_search_intent_filter() -> None:
    row = build_canonical_record("Release strategy chooses staged rollout", "ask")
    row["category"] = "product_decision"
    assert memory_row_browsable(row) is True
    search_decision = evaluate_memory_policy(row, query="unrelated topic", purpose="recall")
    assert search_decision["allowed"] is False
    assert "query_intent_mismatch" in search_decision["reason_codes"]


def test_explicit_browse_still_blocks_labs_and_low_authority_records() -> None:
    labs = build_canonical_record("Experimental candidate", "labs:test")
    assert memory_row_browsable(labs) is False

    inferred = build_canonical_record("Assistant inferred a preference", "mcp:assistant")
    inferred["authority"] = "assistant_inferred"
    inferred["provenance"]["verification_state"] = "unverified"
    inferred["provenance"]["confidence"] = 0.2
    assert memory_row_browsable(inferred) is False
