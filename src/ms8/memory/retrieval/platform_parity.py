"""Cross-platform parity validation for the frozen Hybrid Retrieval v1 contract.

This module reuses the macOS reference fixture and ranking implementation. Platform
specific checks are limited to filesystem, locking, SQLite, and process boundaries;
ranking semantics are never forked.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..infrastructure.durable_io import (
    FileLockTimeoutError,
    exclusive_file_lock,
    replace_path,
)
from ..infrastructure.projection_io import atomic_write_json, read_json_object
from .reference_acceptance import run_reference_acceptance
from .trace_parity import capture_trace_parity

WINDOWS_PARITY_SCHEMA = "ms8.hybrid_windows_parity.v1"


@dataclass(frozen=True, slots=True)
class WindowsParityArtifacts:
    report_json: Path
    report_markdown: Path
    report: Mapping[str, Any]


def _load_json(path: Path, expected_schema: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    if payload.get("schema") != expected_schema:
        raise ValueError(f"{path} schema mismatch")
    return payload


def _projection_replace_roundtrip(workspace: Path) -> tuple[bool, list[str]]:
    projection_root = workspace / "memory" / "projections"
    names = (
        "memory.sqlite3",
        "search.json",
        "fts.json",
        "vector.json",
        "graph.json",
    )
    replaced: list[str] = []
    for name in names:
        path = projection_root / name
        before = path.read_bytes()
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.replacement")
        temporary.write_bytes(before)
        replace_path(temporary, path)
        if path.read_bytes() != before:
            return False, replaced
        replaced.append(name)
    return True, replaced


def _sqlite_quick_check(workspace: Path) -> bool:
    database = workspace / "memory" / "projections" / "memory.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute("PRAGMA quick_check").fetchone()
    return bool(row and row[0] == "ok")


def _wait_for_file(path: Path, process: subprocess.Popen[str], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.02)
    return path.is_file()


def _cross_process_lock_check(workspace: Path) -> bool:
    lock_path = workspace / "memory" / "parity.lock"
    marker = workspace / "memory" / "parity-lock-ready.txt"
    code = (
        "import sys,time\n"
        "from pathlib import Path\n"
        "from ms8.memory.infrastructure.durable_io import exclusive_file_lock\n"
        "lock=Path(sys.argv[1]); marker=Path(sys.argv[2])\n"
        "with exclusive_file_lock(lock, timeout=5.0):\n"
        "    marker.parent.mkdir(parents=True, exist_ok=True)\n"
        "    marker.write_text('ready', encoding='utf-8')\n"
        "    time.sleep(0.8)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(lock_path), str(marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        if not _wait_for_file(marker, process, 5.0):
            process.communicate(timeout=2.0)
            return False
        blocked = False
        try:
            with exclusive_file_lock(lock_path, timeout=0.1, poll_interval=0.02):
                pass
        except FileLockTimeoutError:
            blocked = True
        process.communicate(timeout=5.0)
        if process.returncode != 0 or not blocked:
            return False
        with exclusive_file_lock(lock_path, timeout=1.0):
            return True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2.0)


def _interrupted_write_check(workspace: Path) -> bool:
    state_path = workspace / "memory" / "parity-state.json"
    expected = {"schema": "ms8.hybrid_parity_state.v1", "sequence": 1}
    atomic_write_json(state_path, expected)
    orphan = state_path.with_name(f".{state_path.name}.interrupted.tmp")
    orphan.write_text('{"schema":"broken"', encoding="utf-8")
    observed = read_json_object(state_path)
    orphan.unlink(missing_ok=True)
    return observed == expected


def _required_keys(payload: Mapping[str, Any], keys: Sequence[str]) -> bool:
    return set(keys) == set(payload)


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Hybrid Retrieval v1 Windows parity",
        "",
        f"- Platform: `{report['platform']}`",
        f"- Accepted: `{report['accepted']}`",
        "",
        "## Gates",
        "",
    ]
    gates = report.get("gates", {})
    if isinstance(gates, Mapping):
        lines.extend(
            f"- [{'x' if bool(value) else ' '}] `{name}`"
            for name, value in gates.items()
        )
    lines.extend(
        [
            "",
            "## Replaced projection files",
            "",
        ]
    )
    replaced = report.get("replaced_projection_files", ())
    if isinstance(replaced, Sequence) and not isinstance(replaced, (str, bytes, bytearray)):
        lines.extend(f"- `{value}`" for value in replaced)
    lines.append("")
    return "\n".join(lines)


def run_windows_parity(
    fixture_path: Path,
    contract_path: Path,
    output_dir: Path,
    *,
    workspace: Path,
    platform_name: str = "Windows",
) -> WindowsParityArtifacts:
    """Run the frozen retrieval suite and Windows-specific IO boundary checks."""

    workspace = Path(workspace)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    contract = _load_json(contract_path, "ms8.hybrid_public_contract.v1")
    reference = run_reference_acceptance(
        fixture_path,
        output_dir / "reference",
        workspace=workspace,
        platform_name=platform_name,
    )
    trace_workspace = workspace.with_name(f"{workspace.name} trace parity")
    trace_report = capture_trace_parity(
        fixture_path,
        workspace=trace_workspace,
    )
    reference_report = reference.report
    comparison = reference_report.get("comparison")
    release_gates = reference_report.get("release_gates")
    if not isinstance(comparison, Mapping) or not isinstance(release_gates, Mapping):
        raise TypeError("reference acceptance report is incomplete")
    hybrid = comparison.get("hybrid")
    if not isinstance(hybrid, Mapping):
        raise TypeError("reference acceptance hybrid report is incomplete")
    metrics = hybrid.get("metrics")
    if not isinstance(metrics, Mapping):
        raise TypeError("reference acceptance metrics are incomplete")

    replacement_ok, replaced = _projection_replace_roundtrip(workspace)
    non_platform_gates = {
        key: bool(value)
        for key, value in release_gates.items()
        if key != "platform_is_macos"
    }
    gates = {
        "platform_is_windows": platform_name == "Windows",
        "public_contract_schema_matches": (
            reference_report.get("schema") == contract.get("reference_report_schema")
            and reference_report.get("fixture_schema") == contract.get("fixture_schema")
            and comparison.get("schema") == contract.get("evaluation_schema")
            and trace_report.get("schema") == contract.get("trace_parity_schema")
        ),
        "required_metrics_match": _required_keys(
            metrics,
            tuple(str(value) for value in contract.get("required_metrics", ())),
        ),
        "frozen_ordering_matches": (
            reference_report.get("golden_ordering") == contract.get("golden_ordering")
        ),
        "frozen_trace_fingerprints_match": (
            trace_report.get("fingerprints") == contract.get("trace_fingerprints")
        ),
        "reference_non_platform_gates_pass": all(non_platform_gates.values()),
        "unicode_and_space_path": (
            " " in str(workspace) and any(ord(character) > 127 for character in str(workspace))
        ),
        "projection_replace_roundtrip": replacement_ok,
        "sqlite_quick_check": _sqlite_quick_check(workspace),
        "cross_process_file_lock": _cross_process_lock_check(workspace),
        "interrupted_write_preserves_committed_state": _interrupted_write_check(workspace),
        "optional_embedding_degradation_is_safe": (
            float(metrics.get("degradation_correctness", 0.0)) == 1.0
            and float(metrics.get("unauthorized_inactive_error_recall_rate", 1.0)) == 0.0
        ),
    }
    report: dict[str, Any] = {
        "schema": WINDOWS_PARITY_SCHEMA,
        "platform": platform_name,
        "contract_schema": contract.get("schema"),
        "reference_report_schema": reference_report.get("schema"),
        "trace_parity_schema": trace_report.get("schema"),
        "gates": gates,
        "accepted": all(gates.values()),
        "golden_ordering": reference_report.get("golden_ordering", {}),
        "trace_fingerprints": trace_report.get("fingerprints", {}),
        "replaced_projection_files": replaced,
        "reference_release_gates": dict(release_gates),
        "reference_metrics": dict(metrics),
    }
    json_path = output_dir / "windows_parity.json"
    markdown_path = output_dir / "windows_parity.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return WindowsParityArtifacts(
        report_json=json_path,
        report_markdown=markdown_path,
        report=report,
    )


__all__ = [
    "WINDOWS_PARITY_SCHEMA",
    "WindowsParityArtifacts",
    "run_windows_parity",
]
