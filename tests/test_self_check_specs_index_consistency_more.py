from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _Searcher:
    def __init__(self, doc_count: int) -> None:
        self._doc_count = doc_count

    def __enter__(self) -> "_Searcher":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def doc_count(self) -> int:
        return self._doc_count


class _WhooshIndex:
    def __init__(self, doc_count: int) -> None:
        self._doc_count = doc_count

    def searcher(self) -> _Searcher:
        return _Searcher(self._doc_count)


class _Core:
    def __init__(self, memory_dir: Path, allow_categories: list[str] | None = None, doc_count: int = 0) -> None:
        self.config = {"memory_dir": str(memory_dir)}
        self.whoosh_search = SimpleNamespace(ix=_WhooshIndex(doc_count))
        cfg = SimpleNamespace(allow_categories=allow_categories or [])
        self.auto_memory = SimpleNamespace(pipeline=SimpleNamespace(config=cfg))


def _write_records(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _write_index(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_l2_index_consistency_missing_incremental_warn(tmp_path: Path) -> None:
    core = _Core(tmp_path, allow_categories=["preference"], doc_count=3)
    _write_records(
        tmp_path / "auto_memory_records.jsonl",
        [
            {"status": "accepted", "category": "preference"},
            {"status": "rejected", "category": "preference"},
        ],
    )
    out = cs._check_l2_index_consistency(core, {})
    assert out["status"] == "warn"
    assert "incremental index missing" in out["message"]


def test_l2_index_consistency_small_sample_ok(tmp_path: Path) -> None:
    core = _Core(tmp_path, allow_categories=["preference"], doc_count=10)
    _write_records(
        tmp_path / "auto_memory_records.jsonl",
        [
            {"status": "accepted", "category": "preference"},
            {"status": "pending_review", "category": "preference"},
            {"status": "accepted", "category": "other"},
        ],
    )
    _write_index(
        tmp_path / "auto_memory_index.json",
        [
            {"status": "accepted", "excluded": False},
            {"status": "pending_review", "excluded": False},
            {"status": "accepted", "excluded": True},
        ],
    )
    out = cs._check_l2_index_consistency(core, {})
    assert out["status"] == "pass"


def test_l2_index_consistency_large_delta_fail(tmp_path: Path) -> None:
    core = _Core(tmp_path, allow_categories=None, doc_count=200)
    records = [{"status": "accepted", "category": "x"} for _ in range(120)]
    _write_records(tmp_path / "auto_memory_records.jsonl", records)
    # Much smaller effective index => ratio > 0.20
    _write_index(
        tmp_path / "auto_memory_index.json",
        [{"status": "accepted", "excluded": False} for _ in range(20)],
    )
    out = cs._check_l2_index_consistency(core, {})
    assert out["status"] == "fail"

