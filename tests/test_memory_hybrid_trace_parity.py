from __future__ import annotations

import json
from pathlib import Path

from ms8.memory.retrieval.trace_parity import (
    TRACE_PARITY_SCHEMA,
    capture_trace_parity,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "memory_hybrid_v1"
FIXTURE_PATH = FIXTURE_ROOT / "public_evaluation_v1.json"
CONTRACT_PATH = FIXTURE_ROOT / "public_contract_v1.json"


def test_trace_fingerprints_freeze_plans_eligibility_scores_and_tie_breaks(tmp_path: Path) -> None:
    report = capture_trace_parity(
        FIXTURE_PATH,
        workspace=tmp_path / "trace workspace",
    )
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert report["schema"] == TRACE_PARITY_SCHEMA
    assert report["schema"] == contract["trace_parity_schema"]
    assert report["fingerprints"] == contract["trace_fingerprints"]
    assert set(report["snapshots"]) == set(contract["golden_ordering"])

    historical = report["snapshots"]["historical_release_rule"]
    assert historical["plan"]["plan"]["query"]["purpose"] == "historical"
    assert historical["eligibility"]["eligible_claim_ids"]
    assert historical["source_hits"]
    assert historical["reranking"]["ranked"]
    assert historical["results"][0]["ranking_explanation"]

    conflict = report["snapshots"]["retention_conflict"]
    assert len(conflict["results"]) == 2
    assert all(row["conflicts"] for row in conflict["results"])
