from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.app.memory.repository import MemoryRepository
from ms8.app.schemas.pipeline_schema import MemoryRecord


def _mk_record(
    *,
    text: str,
    category: str = "test",
    dedupe_key: str = "k1",
    rec_id: str = "r1",
    created_at: str | None = None,
    status: str = "accepted",
    source: str = "unit",
) -> MemoryRecord:
    return MemoryRecord(
        text=text,
        normalized_text=text.lower(),
        category=category,
        confidence=0.8,
        status=status,
        source=source,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        meta={"id": rec_id, "dedupe_key": dedupe_key},
    )


def test_repository_init_and_save_and_list_recent(tmp_path: Path):
    store = tmp_path / "memories.jsonl"
    repo = MemoryRepository(store)
    assert store.exists()

    repo.save(_mk_record(text="A", rec_id="1", dedupe_key="k1"))
    repo.save(_mk_record(text="B", rec_id="2", dedupe_key="k2"))

    recent = repo.list_recent(limit=2)
    assert len(recent) == 2
    assert recent[0]["meta"]["id"] == "2"
    assert recent[1]["meta"]["id"] == "1"


def test_parse_iso_utc_variants_and_invalid(tmp_path: Path):
    repo = MemoryRepository(tmp_path / "m.jsonl")
    z = repo._parse_iso_utc("2026-05-18T00:00:00Z")
    naive = repo._parse_iso_utc("2026-05-18T00:00:00")
    bad = repo._parse_iso_utc("not-a-datetime")

    assert z is not None and z.tzinfo is not None
    assert naive is not None and naive.tzinfo is not None
    assert bad is None


def test_duplicate_finders(tmp_path: Path):
    repo = MemoryRepository(tmp_path / "m.jsonl")
    now = datetime.now(timezone.utc)
    old = (now - timedelta(minutes=20)).isoformat()
    fresh = now.isoformat()

    repo.save(_mk_record(text="old", rec_id="o1", dedupe_key="dup", created_at=old))
    repo.save(_mk_record(text="new", rec_id="n1", dedupe_key="dup", created_at=fresh))
    repo.save(_mk_record(text="other", rec_id="x1", dedupe_key="other", created_at=fresh))

    dup_id = repo.find_duplicate("dup")
    dups = repo.find_duplicates("dup")
    recent_dups = repo.find_recent_duplicates("dup", within_minutes=5)

    assert dup_id in {"o1", "n1"}
    assert len(dups) == 2
    assert len(recent_dups) == 1
    assert recent_dups[0]["meta"]["id"] == "n1"


def test_find_recent_by_category_and_cleanup(tmp_path: Path):
    repo = MemoryRepository(tmp_path / "m.jsonl")
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=3)).isoformat()
    fresh = now.isoformat()

    repo.save(_mk_record(text="kept", rec_id="a1", category="pref", created_at=fresh))
    repo.save(_mk_record(text="old", rec_id="a2", category="pref", created_at=old))
    repo.save(_mk_record(text="rej", rec_id="a3", status="rejected", source="unit-test", created_at=fresh))
    repo.save(_mk_record(text="src", rec_id="a4", source="debug-trace", created_at=fresh))

    recent_pref = repo.find_recent_by_category("pref", within_minutes=90)
    assert len(recent_pref) == 1
    assert recent_pref[0]["meta"]["id"] == "a1"

    report = repo.cleanup(excluded_source_prefixes=["debug"], drop_rejected=True)
    assert report["before"] == 4
    assert report["removed_source"] == 1
    assert report["removed_rejected"] == 1
    assert report["after"] == 2


def test_cleanup_tolerates_bad_json_lines(tmp_path: Path):
    store = tmp_path / "m.jsonl"
    store.write_text('{"id":"1","status":"accepted","source":"ok"}\n{bad-json}\n', encoding="utf-8")
    repo = MemoryRepository(store)
    report = repo.cleanup()
    assert report["before"] == 2
    assert report["after"] == 1
    rows = [json.loads(x) for x in store.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
