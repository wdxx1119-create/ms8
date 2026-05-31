from __future__ import annotations

import json
from pathlib import Path

from ms8.compression_governance import cluster_duplicate_records


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def test_cluster_duplicate_records_marks_superseded(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    report = tmp_path / "report.json"
    _write_jsonl(
        records,
        [
            {
                "id": "1",
                "text": "same content",
                "normalized_text": "same content",
                "status": "accepted",
                "meta": {},
            },
            {
                "id": "2",
                "text": "same content",
                "normalized_text": "same content",
                "status": "accepted",
                "meta": {},
            },
            {
                "id": "3",
                "text": "unique",
                "normalized_text": "unique",
                "status": "accepted",
                "meta": {},
            },
        ],
    )
    out = cluster_duplicate_records(records_file=records, report_file=report)
    assert out["status"] == "success"
    assert out["duplicate_clusters"] == 1
    assert out["superseded_records"] == 1

    rows = [json.loads(x) for x in records.read_text(encoding="utf-8").splitlines() if x.strip()]
    dup = [r for r in rows if r.get("normalized_text") == "same content"]
    assert len(dup) == 2
    assert any(r.get("status") == "superseded" for r in dup)
    assert any(int(r.get("evidence_count", 0) or 0) >= 2 for r in dup)


def test_cluster_duplicate_records_empty_input(tmp_path: Path) -> None:
    records = tmp_path / "empty.jsonl"
    report = tmp_path / "report.json"
    out = cluster_duplicate_records(records_file=records, report_file=report)
    assert out["status"] == "skipped"
    assert report.exists()


def test_cluster_duplicate_records_collapse_and_archive(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    report = tmp_path / "report.json"
    archive = tmp_path / "archive.jsonl"
    _write_jsonl(
        records,
        [
            {"id": "x1", "text": "dup", "normalized_text": "dup", "status": "accepted", "meta": {}},
            {"id": "x2", "text": "dup", "normalized_text": "dup", "status": "accepted", "meta": {}},
        ],
    )
    out = cluster_duplicate_records(
        records_file=records,
        report_file=report,
        collapse_superseded=True,
        superseded_archive_file=archive,
    )
    assert out["status"] == "success"
    assert out["collapsed"] is True
    assert out["archived_superseded"] == 1
    rows = [json.loads(x) for x in records.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    assert archive.exists()
