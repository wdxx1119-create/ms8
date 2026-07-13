from __future__ import annotations

import json
from pathlib import Path

from ms8.memory.application.legacy_migration import prepare_legacy_migration
from ms8.memory.operations_cli import main
from ms8.memory.runtime_format import LEDGER_V1_ENV_FLAG, load_runtime_format_manifest

RECORDED_AT = "2026-07-12T14:00:00+00:00"
MUTATED_AT = "2026-07-12T14:10:00+00:00"


def _rows() -> list[dict[str, object]]:
    return [
        {
            "id": "operations-1",
            "text": "User prefers guarded ledger operations",
            "normalized_text": "User prefers guarded ledger operations",
            "category": "user_preference",
            "status": "verified",
            "source": "ask",
            "created_at": "2026-07-01T01:02:03+00:00",
            "meta": {"confidence": 0.96, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "user_explicit",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
            "can_act_on": False,
        },
        {
            "id": "operations-2",
            "text": "Projection rebuilds are explicit",
            "normalized_text": "Projection rebuilds are explicit",
            "category": "product_decision",
            "status": "accepted",
            "source": "system",
            "created_at": "2026-07-02T04:05:06+00:00",
            "meta": {"confidence": 0.9, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "system_observed",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": False,
            "can_act_on": False,
        },
    ]


def _write_source(tmp_path: Path) -> Path:
    path = tmp_path / "legacy.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in _rows()), encoding="utf-8")
    return path


def _invoke(capsys, *args: str) -> tuple[int, dict[str, object]]:
    code = main(list(args))
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, dict)
    return code, payload


def _migrate(tmp_path: Path, capsys) -> tuple[Path, dict[str, object]]:
    workspace = tmp_path / "workspace"
    source = _write_source(tmp_path)
    migration_id = "mig_operations_cli_001"
    prepared = prepare_legacy_migration(_rows(), migration_id=migration_id, recorded_at=RECORDED_AT)
    confirmation = f"APPLY_LEDGER_V1:{migration_id}:{prepared.plan.content_hash}"
    code, payload = _invoke(
        capsys,
        "--workspace",
        str(workspace),
        "migrate",
        "apply",
        "--source-jsonl",
        str(source),
        "--migration-id",
        migration_id,
        "--recorded-at",
        RECORDED_AT,
        "--backup-id",
        "backup_operations_cli_001",
        "--apply",
        "--confirm",
        confirmation,
    )
    assert code == 0
    assert payload["applied"] is True
    return workspace, payload


def test_migration_plan_is_read_only_and_reports_confirmation_inputs(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "workspace"
    source = _write_source(tmp_path)

    code, payload = _invoke(
        capsys,
        "--workspace",
        str(workspace),
        "migrate",
        "plan",
        "--source-jsonl",
        str(source),
        "--migration-id",
        "mig_operations_plan_001",
        "--recorded-at",
        RECORDED_AT,
    )

    assert code == 0
    assert payload["dry_run"] is True
    assert payload["plan"]["source_count"] == 2
    assert not (workspace / "memory" / "runtime-format.json").exists()


def test_apply_migration_and_ledger_doctor_are_explicit(tmp_path: Path, capsys) -> None:
    workspace, payload = _migrate(tmp_path, capsys)
    manifest = load_runtime_format_manifest(workspace / "memory" / "runtime-format.json")

    assert manifest.ledger_head == payload["target_manifest"]["ledger_head"]
    code, doctor = _invoke(capsys, "--workspace", str(workspace), "doctor")
    assert code == 1
    assert doctor["status"] == "degraded"
    assert doctor["reason_codes"] == ["ledger_v1_flag_required"]


def test_rebuild_requires_flag_head_and_exact_confirmation(tmp_path: Path, capsys, monkeypatch) -> None:
    workspace, _ = _migrate(tmp_path, capsys)
    monkeypatch.setenv(LEDGER_V1_ENV_FLAG, "1")
    manifest = load_runtime_format_manifest(workspace / "memory" / "runtime-format.json")
    head = str(manifest.ledger_head)
    search_path = workspace / "memory" / "projections" / "search.json"
    search_path.unlink()

    code, verify = _invoke(capsys, "--workspace", str(workspace), "rebuild", "verify")
    assert code == 1
    assert verify["projection_ready"] is False

    code, dry_run = _invoke(
        capsys,
        "--workspace",
        str(workspace),
        "rebuild",
        "apply",
        "--expected-head",
        head,
    )
    assert code == 0
    assert dry_run["applied"] is False
    assert not search_path.exists()

    code, applied = _invoke(
        capsys,
        "--workspace",
        str(workspace),
        "rebuild",
        "apply",
        "--expected-head",
        head,
        "--apply",
        "--confirm",
        f"REBUILD_PROJECTIONS:{head}",
    )
    assert code == 0
    assert applied["applied"] is True
    assert search_path.is_file()


def test_forget_mutation_is_dry_run_first_and_advances_manifest(tmp_path: Path, capsys, monkeypatch) -> None:
    workspace, _ = _migrate(tmp_path, capsys)
    monkeypatch.setenv(LEDGER_V1_ENV_FLAG, "true")
    manifest_path = workspace / "memory" / "runtime-format.json"
    before = load_runtime_format_manifest(manifest_path)
    prepared = prepare_legacy_migration(
        _rows(),
        migration_id="mig_operations_cli_001",
        recorded_at=RECORDED_AT,
    )
    claim_id = prepared.plan.previews[0].claim_id
    expected_head = str(before.ledger_head)
    confirmation = f"MUTATE_LEDGER_V1:forget:{claim_id}:{expected_head}"

    code, dry_run = _invoke(
        capsys,
        "--workspace",
        str(workspace),
        "mutate",
        "forget",
        "--claim-id",
        claim_id,
        "--expected-head",
        expected_head,
        "--recorded-at",
        MUTATED_AT,
        "--reason",
        "user requested logical forget",
    )
    assert code == 0
    assert dry_run["dry_run"] is True
    assert load_runtime_format_manifest(manifest_path) == before

    code, applied = _invoke(
        capsys,
        "--workspace",
        str(workspace),
        "mutate",
        "forget",
        "--claim-id",
        claim_id,
        "--expected-head",
        expected_head,
        "--recorded-at",
        MUTATED_AT,
        "--reason",
        "user requested logical forget",
        "--apply",
        "--confirm",
        confirmation,
    )
    after = load_runtime_format_manifest(manifest_path)
    assert code == 0
    assert applied["applied"] is True
    assert after.generation == before.generation + 1
    assert after.ledger_head == applied["new_head"]

    code, query = _invoke(
        capsys,
        "--workspace",
        str(workspace),
        "query",
        "guarded ledger operations",
    )
    assert code == 0
    assert all(row["id"] != claim_id for row in query["results"])


def test_rollback_dry_run_requires_explicit_backup_target_identity(tmp_path: Path, capsys, monkeypatch) -> None:
    workspace, migration = _migrate(tmp_path, capsys)
    monkeypatch.setenv(LEDGER_V1_ENV_FLAG, "1")
    manifest = load_runtime_format_manifest(workspace / "memory" / "runtime-format.json")
    source = tmp_path / "legacy.jsonl"

    code, payload = _invoke(
        capsys,
        "--workspace",
        str(workspace),
        "migrate",
        "rollback",
        "--backup-path",
        str(migration["backup_path"]),
        "--backup-target",
        f"legacy_source={source}",
        "--expected-head",
        str(manifest.ledger_head),
    )

    assert code == 0
    assert payload["dry_run"] is True
    assert payload["applied"] is False
    assert load_runtime_format_manifest(workspace / "memory" / "runtime-format.json") == manifest
