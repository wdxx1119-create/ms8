"""Run the complete isolated memory-ledger validation gate.

This script is intentionally cross-platform so the same commands run in
isolated macOS and Windows validation environments. It does not touch
production memory paths or enable ledger-v1.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_MEMORY_TARGETS = (
    "src/ms8/record_policy.py",
    "src/ms8/memory_safety.py",
)
CLI_MCP_RUFF_TARGETS = (
    "src/ms8/cli.py",
    "src/ms8/connect/mcp_server/mcp_server.py",
    "src/ms8/connect/mcp_server/memory_service_interface.py",
    "src/ms8/connect/mcp_server/stdio_server.py",
)
TEST_MODULES = (
    "tests/test_memory_ledger_domain.py",
    "tests/test_memory_jsonl_record_store.py",
    "tests/test_memory_ledger_schema.py",
    "tests/test_memory_replay_projection.py",
    "tests/test_memory_injectable_evidence_invariant.py",
    "tests/test_memory_replay_incremental_invariant.py",
    "tests/test_memory_projection_coordinator.py",
    "tests/test_memory_projection_fail_closed.py",
    "tests/test_memory_runtime_format.py",
    "tests/test_memory_lifecycle_time_conflicts.py",
    "tests/test_memory_supersede_valid_time.py",
    "tests/test_memory_forget_projections.py",
    "tests/test_memory_conflict_recording.py",
    "tests/test_memory_legacy_migration.py",
    "tests/test_memory_production_characterization.py",
    "tests/test_memory_cli_mcp_characterization.py",
    "tests/test_memory_production_migration_controls.py",
    "tests/test_memory_physical_purge_controls.py",
    "tests/test_memory_retrieval_context.py",
    "tests/test_memory_ledger_compatibility_adapter.py",
    "tests/test_memory_ledger_cli_explain.py",
    "tests/test_memory_projection_recovery.py",
    "tests/test_memory_destructive_recovery_scenarios.py",
    "tests/test_memory_release_gate.py",
    "tests/test_memory_operations_cli.py",
    "tests/test_memory_performance_baseline.py",
    "tests/test_memory_public_safety.py",
    "tests/test_memory_final_integration.py",
    "tests/test_memory_macos_stability_acceptance.py",
    "tests/test_memory_windows_durable_io.py",
    "tests/test_memory_windows_platform_contract.py",
    "tests/test_memory_windows_cli_mcp_process.py",
)


@dataclass(frozen=True, slots=True)
class ValidationStage:
    name: str
    command: tuple[str, ...]
    environment: dict[str, str] | None = None


def _run(stage: ValidationStage) -> int:
    print(f"\n=== {stage.name} ===", flush=True)
    print("$ " + " ".join(stage.command), flush=True)
    completed = subprocess.run(
        stage.command,
        cwd=REPOSITORY_ROOT,
        env=stage.environment,
        check=False,
    )
    return completed.returncode


def main() -> int:
    pytest_environment = os.environ.copy()
    pytest_environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")

    stages = (
        ValidationStage(
            name="ruff",
            command=(
                sys.executable,
                "-m",
                "ruff",
                "check",
                "src/ms8/memory",
                *PRODUCTION_MEMORY_TARGETS,
                *CLI_MCP_RUFF_TARGETS,
                *TEST_MODULES,
            ),
        ),
        ValidationStage(
            name="mypy",
            command=(
                sys.executable,
                "-m",
                "mypy",
                "src/ms8/memory",
                *PRODUCTION_MEMORY_TARGETS,
            ),
        ),
        ValidationStage(
            name="pytest",
            command=(sys.executable, "-m", "pytest", "-q", "-o", "addopts=", *TEST_MODULES),
            environment=pytest_environment,
        ),
    )

    results = [(stage.name, _run(stage)) for stage in stages]

    print("\n=== validation summary ===")
    for name, return_code in results:
        outcome = "PASS" if return_code == 0 else f"FAIL ({return_code})"
        print(f"{name:8} {outcome}")

    failed = [name for name, return_code in results if return_code != 0]
    if failed:
        print("Failed stages: " + ", ".join(failed))
        return 1
    print("All isolated memory-ledger validation stages passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
