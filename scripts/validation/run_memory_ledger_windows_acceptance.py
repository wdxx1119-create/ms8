"""Run the Windows-specific Memory Ledger v1 acceptance criteria.

The runner writes evidence into an explicitly supplied directory and never discovers
or accesses a real MS8 runtime. It is intended for an isolated Windows runner.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Criterion:
    criterion_id: str
    title: str
    tests: tuple[str, ...]


CRITERIA = (
    Criterion(
        "AC01",
        "migration dry-run backup rollback audit",
        (
            "tests/test_memory_production_migration_controls.py",
            "tests/test_memory_operations_cli.py",
        ),
    ),
    Criterion(
        "AC02",
        "injectable evidence provenance invariant",
        (
            "tests/test_memory_injectable_evidence_invariant.py",
            "tests/test_memory_replay_incremental_invariant.py",
        ),
    ),
    Criterion(
        "AC03",
        "append-only lifecycle operations",
        (
            "tests/test_memory_lifecycle_time_conflicts.py",
            "tests/test_memory_supersede_valid_time.py",
            "tests/test_memory_forget_projections.py",
        ),
    ),
    Criterion(
        "AC04",
        "conflict candidates and recommendation",
        (
            "tests/test_memory_conflict_recording.py",
            "tests/test_memory_retrieval_context.py",
        ),
    ),
    Criterion(
        "AC05",
        "recorded observed and valid-time independence",
        ("tests/test_memory_macos_stability_acceptance.py::test_recorded_observed_and_valid_time_are_independent",),
    ),
    Criterion(
        "AC06",
        "all disposable projections rebuild from ledger",
        (
            "tests/test_memory_projection_recovery.py",
            "tests/test_memory_windows_platform_contract.py::test_unicode_space_workspace_rebuilds_every_projection",
        ),
    ),
    Criterion(
        "AC07",
        "legacy CLI and MCP compatibility",
        (
            "tests/test_memory_cli_mcp_characterization.py",
            "tests/test_memory_ledger_compatibility_adapter.py",
            "tests/test_memory_ledger_cli_explain.py",
        ),
    ),
    Criterion(
        "AC08",
        "invalid and incomplete transactions fail closed",
        (
            "tests/test_memory_jsonl_record_store.py",
            "tests/test_memory_projection_fail_closed.py",
        ),
    ),
    Criterion(
        "AC09",
        "automated lifecycle operations require PolicyEngine grant",
        ("tests/test_memory_macos_stability_acceptance.py::test_automated_lifecycle_requires_verified_policyengine_grant",),
    ),
    Criterion(
        "AC10",
        "physical purge reports residual backup scope",
        ("tests/test_memory_physical_purge_controls.py",),
    ),
    Criterion(
        "WIN01",
        "Windows atomic replace lock and Unicode path behavior",
        ("tests/test_memory_windows_durable_io.py",),
    ),
    Criterion(
        "WIN02",
        "Windows workspace and projection path contract",
        ("tests/test_memory_windows_platform_contract.py",),
    ),
    Criterion(
        "WIN03",
        "destructive recovery under Windows filesystem semantics",
        ("tests/test_memory_destructive_recovery_scenarios.py",),
    ),
    Criterion(
        "WIN04",
        "installed CLI and MCP stdio process lifecycle on Windows",
        ("tests/test_memory_windows_cli_mcp_process.py",),
    ),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--logs-dir", type=Path, required=True)
    return parser


def _run(criterion: Criterion, logs_dir: Path) -> dict[str, object]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{criterion.criterion_id}.log"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-o",
        "addopts=",
        *criterion.tests,
    ]
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log_path.write_text(
        "$ " + " ".join(command) + "\n\n" + completed.stdout + completed.stderr,
        encoding="utf-8",
    )
    return {
        "id": criterion.criterion_id,
        "title": criterion.title,
        "passed": completed.returncode == 0,
        "exit_code": completed.returncode,
        "tests": list(criterion.tests),
        "log": log_path.as_posix(),
    }


def main() -> int:
    args = _parser().parse_args()
    rows = [_run(criterion, args.logs_dir) for criterion in CRITERIA]
    is_windows = os.name == "nt" and platform.system() == "Windows"
    passed = is_windows and all(bool(row["passed"]) for row in rows)
    payload = {
        "schema": "ms8.memory-ledger-windows-acceptance.v1",
        "candidate_sha": args.candidate_sha,
        "platform": platform.system(),
        "platform_release": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "is_windows": is_windows,
        "criteria": rows,
        "passed": passed,
        "status": "WINDOWS_READY_FOR_REVIEW" if passed else "HOLD",
        "real_user_runtime_accessed": False,
        "ledger_v1_enabled_by_default": False,
        "production_runtime_format_switched": False,
        "pypi_publish_performed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
