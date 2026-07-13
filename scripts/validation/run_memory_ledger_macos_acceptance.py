"""Run the ten ledger-v1 stability acceptance criteria on an isolated macOS candidate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class AcceptanceCriterion:
    criterion_id: str
    title: str
    nodeids: tuple[str, ...]


CRITERIA = (
    AcceptanceCriterion(
        "AC01",
        "migration dry-run, verified backup, rollback, and audit evidence",
        ("tests/test_memory_production_migration_controls.py",),
    ),
    AcceptanceCriterion(
        "AC02",
        "injectable claims require source, hash, locator evidence, and decision trace",
        (
            "tests/test_memory_injectable_evidence_invariant.py",
            "tests/test_memory_macos_stability_acceptance.py::test_injectable_replacement_without_new_evidence_is_rejected_before_append",
        ),
    ),
    AcceptanceCriterion(
        "AC03",
        "correct, supersede, revoke, and forget remain append-only",
        (
            "tests/test_memory_lifecycle_time_conflicts.py::test_correct_appends_replacement_without_mutating_original",
            "tests/test_memory_lifecycle_time_conflicts.py::test_forget_hides_content_and_can_return_minimal_tombstone",
            "tests/test_memory_lifecycle_time_conflicts.py::test_resolve_conflict_keeps_losing_claim_auditable",
        ),
    ),
    AcceptanceCriterion(
        "AC04",
        "conflicts retain candidates and return deterministic recommendations",
        (
            "tests/test_memory_lifecycle_time_conflicts.py::test_conflict_detection_retains_alternatives_and_explains_recommendation",
            "tests/test_memory_macos_stability_acceptance.py::test_query_explain_and_context_return_complete_conflict_details",
        ),
    ),
    AcceptanceCriterion(
        "AC05",
        "recorded, observed, and valid time coordinates are independent",
        (
            "tests/test_memory_lifecycle_time_conflicts.py::test_as_of_query_separates_recorded_time_from_current_state",
            "tests/test_memory_macos_stability_acceptance.py::test_recorded_observed_and_valid_time_are_independent",
        ),
    ),
    AcceptanceCriterion(
        "AC06",
        "SQLite, FTS, vector, search, and graph projections rebuild from ledger",
        (
            "tests/test_memory_macos_stability_acceptance.py::test_sqlite_fts_vector_graph_and_search_rebuild_from_ledger",
            "tests/test_memory_projection_recovery.py::test_invalid_ledger_refuses_rebuild_without_touching_projections",
        ),
    ),
    AcceptanceCriterion(
        "AC07",
        "legacy CLI and MCP primary behavior remains compatible",
        (
            "tests/test_memory_cli_mcp_characterization.py",
            "tests/test_memory_ledger_compatibility_adapter.py",
            "tests/test_memory_ledger_cli_explain.py",
            "tests/test_memory_final_integration.py",
        ),
    ),
    AcceptanceCriterion(
        "AC08",
        "uncommitted, hash-broken, or schema-invalid transactions never enter projections",
        (
            "tests/test_memory_jsonl_record_store.py::test_truncated_tail_is_reported_and_excluded_from_iteration",
            "tests/test_memory_jsonl_record_store.py::test_hash_broken_transaction_never_enters_valid_prefix",
            "tests/test_memory_jsonl_record_store.py::test_sequence_gap_never_enters_valid_prefix",
            "tests/test_memory_projection_fail_closed.py",
        ),
    ),
    AcceptanceCriterion(
        "AC09",
        "automated lifecycle maintenance cannot bypass PolicyEngine authorization",
        (
            "tests/test_memory_macos_stability_acceptance.py::test_automated_lifecycle_requires_verified_policyengine_grant",
        ),
    ),
    AcceptanceCriterion(
        "AC10",
        "physical purge reports backup residual scope without false deletion claims",
        ("tests/test_memory_physical_purge_controls.py",),
    ),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--logs-dir", type=Path, required=True)
    parser.add_argument("--candidate-sha", default="")
    return parser


def _log_reference(log_path: Path) -> str:
    try:
        return log_path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    except ValueError:
        return log_path.name


def _run(criterion: AcceptanceCriterion, logs_dir: Path) -> dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-o",
        "addopts=",
        *criterion.nodeids,
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{criterion.criterion_id}.log"
    log_path.write_text(completed.stdout, encoding="utf-8")
    return {
        "criterion_id": criterion.criterion_id,
        "title": criterion.title,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "exit_code": completed.returncode,
        "nodeids": list(criterion.nodeids),
        "log": _log_reference(log_path),
    }


def main() -> int:
    args = _parser().parse_args()
    results = [_run(criterion, args.logs_dir) for criterion in CRITERIA]
    passed = all(item["status"] == "PASS" for item in results)
    payload = {
        "schema": "ms8.memory-ledger-macos-stability-acceptance.v1",
        "candidate_sha": args.candidate_sha,
        "platform": "macOS",
        "python": sys.version.split()[0],
        "status": "MACOS_READY_FOR_REVIEW" if passed else "HOLD",
        "passed": passed,
        "criteria": results,
        "real_user_runtime_accessed": False,
        "runtime_format_switched": False,
        "ledger_v1_enabled_by_default": False,
        "public_repository_modified": False,
        "pypi_publish_performed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
