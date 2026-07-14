from __future__ import annotations

import json
from pathlib import Path

from ms8.memory.retrieval.platform_parity import (
    WINDOWS_PARITY_SCHEMA,
    run_windows_parity,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "memory_hybrid_v1"
FIXTURE_PATH = FIXTURE_ROOT / "public_evaluation_v1.json"
CONTRACT_PATH = FIXTURE_ROOT / "public_contract_v1.json"


def test_windows_parity_reuses_frozen_ranking_and_validates_io_boundaries(tmp_path: Path) -> None:
    artifacts = run_windows_parity(
        FIXTURE_PATH,
        CONTRACT_PATH,
        tmp_path / "artifacts",
        workspace=tmp_path / "MS8 Hybrid 中文 workspace",
        platform_name="Windows",
    )

    report = artifacts.report
    assert report["schema"] == WINDOWS_PARITY_SCHEMA
    assert report["platform"] == "Windows"
    assert report["accepted"] is True
    assert all(report["gates"].values())
    assert report["replaced_projection_files"] == [
        "memory.sqlite3",
        "search.json",
        "fts.json",
        "vector.json",
        "graph.json",
    ]
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert report["golden_ordering"] == contract["golden_ordering"]
    assert artifacts.report_json.is_file()
    assert artifacts.report_markdown.is_file()


def test_non_windows_label_fails_only_the_platform_gate(tmp_path: Path) -> None:
    artifacts = run_windows_parity(
        FIXTURE_PATH,
        CONTRACT_PATH,
        tmp_path / "artifacts",
        workspace=tmp_path / "MS8 Hybrid 中文 workspace",
        platform_name="Linux",
    )

    assert artifacts.report["accepted"] is False
    assert artifacts.report["gates"]["platform_is_windows"] is False
    remaining = {
        key: value
        for key, value in artifacts.report["gates"].items()
        if key != "platform_is_windows"
    }
    assert all(remaining.values())
