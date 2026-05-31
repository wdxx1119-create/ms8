from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8 import runtime


def _paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "quarantine": root / "memory" / "noncanonical_quarantine.jsonl",
        "health": root / "health",
        "memories": root / "data" / "memories.jsonl",
    }


def test_archive_schema_invalid_history_skips_when_missing(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    (root / "health").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: _paths(root))
    out = runtime.archive_schema_invalid_history(older_than_days=1)
    assert out["status"] == "skipped"
    assert out["reason"] == "quarantine_missing"


def test_archive_schema_invalid_history_archives_old_rows(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    (root / "memory").mkdir(parents=True, exist_ok=True)
    (root / "health").mkdir(parents=True, exist_ok=True)
    qf = _paths(root)["quarantine"]
    old_at = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    new_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    qf.write_text(
        "\n".join(
            [
                json.dumps({"at": old_at, "record": {"id": "old"}}),
                json.dumps({"at": new_at, "record": {"id": "new"}}),
                "{bad-json}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: _paths(root))
    out = runtime.archive_schema_invalid_history(older_than_days=30)
    assert out["status"] == "success"
    assert out["archived"] == 1
    assert out["kept"] == 2
    archive_dir = root / "health" / "archive"
    assert any(p.name.startswith("quarantine_schema_invalid_") for p in archive_dir.iterdir())


def test_repair_quarantine_records_repairs_and_keeps_invalid(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    (root / "memory").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    qf = _paths(root)["quarantine"]
    mem = _paths(root)["memories"]
    mem.write_text(json.dumps({"id": "exists", "text": "existing"}) + "\n", encoding="utf-8")
    rec_fixable = {
        "record": {
            "id": "r1",
            "text": "Hello world",
            "source": "user",
            "normalized_text": "hello world",
            "category": "general",
            "status": "candidate",
            "meta": {"admission": "t"},
            "scope": "global",
            "authority": "user_explicit",
            "sensitivity": "public",
            "can_recall": True,
            "can_inject": True,
            "can_act_on": False,
        }
    }
    rec_dup = {
        "record": {
            "id": "exists",
            "text": "dup",
            "source": "user",
            "normalized_text": "dup",
            "category": "general",
            "status": "candidate",
            "meta": {"admission": "t"},
            "scope": "global",
            "authority": "user_explicit",
            "sensitivity": "public",
            "can_recall": True,
            "can_inject": True,
            "can_act_on": False,
        }
    }
    qf.write_text("\n".join([json.dumps(rec_fixable), json.dumps(rec_dup), "{bad-json}"]) + "\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: _paths(root))
    out = runtime.repair_quarantine_records()
    assert out["status"] == "success"
    assert out["repaired"] == 1
    assert out["skipped"] == 2
    mem_lines = mem.read_text(encoding="utf-8").splitlines()
    assert any('"id": "r1"' in ln for ln in mem_lines)


def test_fallback_error_code_mapping() -> None:
    assert runtime._fallback_error_code_from_reason("core_unavailable") == "E_CORE_UNAVAILABLE"
    assert runtime._fallback_error_code_from_reason("unknown_reason") == "E_FALLBACK_GENERIC"
