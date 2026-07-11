from __future__ import annotations

import json
from pathlib import Path

from ms8.format_registry import (
    CURRENT_RUNTIME_FORMAT_VERSION,
    apply_runtime_migrations,
    load_format_manifest,
    plan_runtime_migrations,
)


def test_legacy_runtime_migration_creates_backup_manifest_and_audit(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    (root / "memory").mkdir(parents=True)
    (root / "memory" / "auto_memory_records.jsonl").write_text('{"id":"m1"}\n', encoding="utf-8")
    (root / "config.json").write_text(json.dumps({"custom": {"keep": True}}), encoding="utf-8")

    plan = plan_runtime_migrations(root)
    assert plan["current_version"] == 0
    assert plan["target_version"] == CURRENT_RUNTIME_FORMAT_VERSION
    assert plan["steps"] == [{"from": 0, "to": 1}]
    assert plan["requires_backup"] is True

    manifest = apply_runtime_migrations(root)
    assert manifest["runtime_format_version"] == CURRENT_RUNTIME_FORMAT_VERSION
    assert manifest["canonical_record_schema_version"] == 1
    assert manifest["config_schema_version"] == 1
    assert (root / "format_manifest.json").is_file()
    assert list((root / "backups").glob("ms8-runtime-pre-migration-*.zip"))
    audit = root / "memory" / "logs" / "migration_audit.jsonl"
    assert audit.is_file()
    event = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert event["from_version"] == 0
    assert event["to_version"] == 1


def test_current_manifest_preserves_unknown_fields(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    payload = {
        "manifest_schema_version": 1,
        "runtime_format_version": CURRENT_RUNTIME_FORMAT_VERSION,
        "canonical_record_schema_version": 1,
        "vendor_extension": {"keep": "yes"},
    }
    (root / "format_manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    result = apply_runtime_migrations(root)
    assert result["vendor_extension"] == {"keep": "yes"}
    assert load_format_manifest(root)["vendor_extension"] == {"keep": "yes"}


def test_runtime_format_downgrade_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "format_manifest.json").write_text(
        json.dumps({"runtime_format_version": CURRENT_RUNTIME_FORMAT_VERSION}),
        encoding="utf-8",
    )

    try:
        plan_runtime_migrations(root, target_version=0)
    except ValueError as exc:
        assert "downgrade" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected downgrade rejection")
