from __future__ import annotations

from pathlib import Path

from ms8.cli import _build_parser
from ms8.memory.doctor_integration import ledger_doctor_status


def test_main_cli_accepts_final_memory_ledger_operations() -> None:
    parser = _build_parser()

    doctor = parser.parse_args(["memory-ledger", "--workspace", "/tmp/ms8", "doctor"])
    assert doctor.memory_ledger_cmd == "doctor"

    rebuild = parser.parse_args(
        [
            "memory-ledger",
            "--workspace",
            "/tmp/ms8",
            "rebuild",
            "apply",
            "--expected-head",
            "sha256:" + "a" * 64,
        ]
    )
    assert rebuild.memory_ledger_cmd == "rebuild"
    assert rebuild.ledger_rebuild_cmd == "apply"
    assert rebuild.apply is False

    mutate = parser.parse_args(
        [
            "memory-ledger",
            "--workspace",
            "/tmp/ms8",
            "mutate",
            "forget",
            "--claim-id",
            "clm_1",
            "--expected-head",
            "sha256:" + "b" * 64,
            "--recorded-at",
            "2026-07-12T16:00:00+00:00",
            "--reason",
            "user requested forget",
        ]
    )
    assert mutate.memory_ledger_cmd == "mutate"
    assert mutate.action == "forget"
    assert mutate.apply is False

    migrate = parser.parse_args(
        [
            "memory-ledger",
            "--workspace",
            "/tmp/ms8",
            "migrate",
            "plan",
            "--source-jsonl",
            "/tmp/legacy.jsonl",
            "--migration-id",
            "mig_1",
            "--recorded-at",
            "2026-07-12T16:00:00+00:00",
        ]
    )
    assert migrate.memory_ledger_cmd == "migrate"
    assert migrate.ledger_migrate_cmd == "plan"


def test_ledger_doctor_stays_read_only_for_legacy_runtime(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    status = ledger_doctor_status(workspace)

    assert status["ok"] is True
    assert status["status"] == "inactive"
    assert status["reason_codes"] == ["legacy_runtime_active"]
    assert not (workspace / "memory" / "ledger-v1").exists()
