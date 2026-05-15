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
