from __future__ import annotations

import json
from pathlib import Path

from ms8.memory.retrieval.reference_acceptance import (
    REFERENCE_REPORT_SCHEMA,
    run_reference_acceptance,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "memory_hybrid_v1"
FIXTURE_PATH = FIXTURE_ROOT / "public_evaluation_v1.json"
CONTRACT_PATH = FIXTURE_ROOT / "public_contract_v1.json"


def test_reference_acceptance_builds_isolated_comparison_report(tmp_path: Path) -> None:
    artifacts = run_reference_acceptance(
        FIXTURE_PATH,
        tmp_path / "artifacts",
        workspace=tmp_path / "workspace with spaces",
        platform_name="Darwin",
    )

    report = artifacts.report
    assert report["schema"] == REFERENCE_REPORT_SCHEMA
    assert report["fixture_schema"] == "ms8.hybrid_evaluation_fixture.v1"
    assert report["platform"] == "Darwin"
    assert report["conflict_count"] >= 1
    assert artifacts.report_json.is_file()
    assert artifacts.report_markdown.is_file()

    persisted = json.loads(artifacts.report_json.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["schema"] == "ms8.hybrid_public_contract.v1"
    assert persisted["schema"] == contract["reference_report_schema"]
    assert persisted["fixture_schema"] == contract["fixture_schema"]
    assert persisted["comparison"]["schema"] == contract["evaluation_schema"]
    assert persisted["comparison"]["baseline"]["case_count"] == 6
    assert persisted["comparison"]["hybrid"]["case_count"] == 6
    assert set(persisted["comparison"]["hybrid"]["metrics"]) == set(
        contract["required_metrics"]
    )
    assert set(persisted["release_gates"]) == set(contract["required_release_gates"])
    assert persisted["golden_ordering"] == contract["golden_ordering"]
    assert persisted["accepted"] is True
    assert persisted["comparison"]["hybrid"]["metrics"][
        "unauthorized_inactive_error_recall_rate"
    ] == 0.0
    assert persisted["comparison"]["hybrid"]["metrics"][
        "evidence_citation_coverage"
    ] == 1.0
    assert persisted["comparison"]["hybrid"]["metrics"]["degradation_correctness"] == 1.0


def test_non_macos_label_cannot_pass_reference_acceptance(tmp_path: Path) -> None:
    artifacts = run_reference_acceptance(
        FIXTURE_PATH,
        tmp_path / "artifacts",
        workspace=tmp_path / "workspace",
        platform_name="Linux",
    )

    assert artifacts.report["release_gates"]["platform_is_macos"] is False
    assert artifacts.report["accepted"] is False
