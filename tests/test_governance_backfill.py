from __future__ import annotations

import json
from pathlib import Path

from ms8.record_policy import repair_scope_flags


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def test_backfill_dry_run_does_not_modify_file(tmp_path: Path) -> None:
    f = tmp_path / "records.jsonl"
    rows = [
        {
            "id": "1",
            "text": "hello",
            "normalized_text": "hello",
            "category": "general",
            "status": "accepted",
            "source": "ask",
            "meta": {"admission": "x"},
        }
    ]
    _write_rows(f, rows)
    before = f.read_text(encoding="utf-8")
    out = repair_scope_flags(f, dry_run=True)
    after = f.read_text(encoding="utf-8")
    assert out["mode"] == "dry_run"
    assert before == after


def test_backfill_apply_repairs_system_debug_inject_flag(tmp_path: Path) -> None:
    f = tmp_path / "records.jsonl"
    rows = [
        {
            "id": "2",
            "text": "pytest traceback on self-check pipeline",
            "normalized_text": "pytest traceback on self-check pipeline",
            "category": "general",
            "status": "accepted",
            "source": "ask",
            "scope": "system_debug",
            "can_inject": True,
            "meta": {"admission": "x"},
        }
    ]
    _write_rows(f, rows)
    out = repair_scope_flags(f, dry_run=False)
    assert out["updated"] >= 1
    row = json.loads(f.read_text(encoding="utf-8").strip())
    assert row["can_inject"] is False
    assert row["can_act_on"] is False
    assert row["schema_version"] == "1.0"
    assert row["migration_version"] == "p0_2_v1"
