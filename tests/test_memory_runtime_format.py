from __future__ import annotations

import json
from pathlib import Path

import pytest

from ms8.memory.runtime_format import (
    LEDGER_V1_ENV_FLAG,
    LEDGER_V1_RUNTIME_FORMAT,
    LEGACY_RUNTIME_FORMAT,
    RUNTIME_FORMAT_SCHEMA,
    RuntimeFormatManifest,
    RuntimeFormatManifestError,
    evaluate_runtime_format,
    load_runtime_format_manifest,
)

FIXED_TIME = "2026-07-12T00:00:00+00:00"
LEDGER_HEAD = "sha256:" + ("1" * 64)


def test_missing_manifest_defaults_to_legacy_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "runtime-format.json"

    manifest = load_runtime_format_manifest(path)
    decision = evaluate_runtime_format(manifest, {})

    assert manifest.active_format == LEGACY_RUNTIME_FORMAT
    assert decision.allowed is True
    assert decision.selected_format == LEGACY_RUNTIME_FORMAT
    assert not path.exists()


def test_environment_flag_alone_never_switches_authoritative_format() -> None:
    manifest = RuntimeFormatManifest(
        schema=RUNTIME_FORMAT_SCHEMA,
        active_format=LEGACY_RUNTIME_FORMAT,
        generation=4,
        updated_at=FIXED_TIME,
    )

    decision = evaluate_runtime_format(manifest, {LEDGER_V1_ENV_FLAG: "true"})

    assert decision.allowed is True
    assert decision.selected_format == LEGACY_RUNTIME_FORMAT
    assert decision.reason == "ledger_v1_flag_armed_but_manifest_remains_legacy"


def test_ledger_manifest_requires_explicit_enablement_flag() -> None:
    manifest = RuntimeFormatManifest(
        schema=RUNTIME_FORMAT_SCHEMA,
        active_format=LEDGER_V1_RUNTIME_FORMAT,
        generation=5,
        updated_at=FIXED_TIME,
        previous_format=LEGACY_RUNTIME_FORMAT,
        migration_id="migration_001",
        ledger_head=LEDGER_HEAD,
    )

    blocked = evaluate_runtime_format(manifest, {})
    enabled = evaluate_runtime_format(manifest, {LEDGER_V1_ENV_FLAG: "1"})

    assert blocked.allowed is False
    assert blocked.reason == "ledger_v1_flag_required"
    assert enabled.allowed is True
    assert enabled.selected_format == LEDGER_V1_RUNTIME_FORMAT


def test_manifest_round_trip_preserves_switch_metadata(tmp_path: Path) -> None:
    path = tmp_path / "runtime-format.json"
    expected = RuntimeFormatManifest(
        schema=RUNTIME_FORMAT_SCHEMA,
        active_format=LEDGER_V1_RUNTIME_FORMAT,
        generation=7,
        updated_at=FIXED_TIME,
        previous_format=LEGACY_RUNTIME_FORMAT,
        migration_id="migration_007",
        ledger_head=LEDGER_HEAD,
    )
    path.write_text(json.dumps(expected.to_dict()), encoding="utf-8")

    restored = load_runtime_format_manifest(path)

    assert restored == expected


def test_invalid_or_incomplete_manifest_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "runtime-format.json"
    path.write_text(
        json.dumps(
            {
                "schema": RUNTIME_FORMAT_SCHEMA,
                "active_format": LEDGER_V1_RUNTIME_FORMAT,
                "generation": 1,
                "updated_at": FIXED_TIME,
                "migration_id": "migration_invalid",
                "ledger_head": None,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeFormatManifestError, match="ledger_head must not be empty"):
        load_runtime_format_manifest(path)


def test_ledger_manifest_rejects_non_sha256_head() -> None:
    with pytest.raises(RuntimeFormatManifestError, match="sha256"):
        RuntimeFormatManifest(
            schema=RUNTIME_FORMAT_SCHEMA,
            active_format=LEDGER_V1_RUNTIME_FORMAT,
            generation=1,
            updated_at=FIXED_TIME,
            migration_id="migration_invalid_hash",
            ledger_head="not-a-hash",
        )
