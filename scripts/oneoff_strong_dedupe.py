from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ms8.record_policy import is_valid_status_transition


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    if payload:
        payload += "\n"
    path.write_text(payload, encoding="utf-8")


def _norm_text(row: dict[str, Any]) -> str:
    raw = str(row.get("normalized_text") or row.get("text") or "").strip()
    return " ".join(raw.split())


def _h(text: str) -> str:
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()


def _status_rank(status: str) -> int:
    order = {
        "verified": 0,
        "accepted": 1,
        "candidate": 2,
        "short_term": 3,
        "pending_review": 4,
        "stale": 5,
        "superseded": 6,
        "revoked": 7,
        "quarantined": 8,
    }
    return order.get(status, 9)


def _canonical_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    status = str(row.get("status") or "accepted")
    confidence = float(row.get("confidence", 0.0) or 0.0)
    created = str(row.get("created_at") or "")
    rid = str(row.get("id") or "")
    return (_status_rank(status), -confidence, created, rid)


def strong_dedupe(
    records_path: Path,
    dry_run: bool = False,
    collapse_superseded: bool = False,
    archive_path: Path | None = None,
) -> dict[str, Any]:
    rows = _read_jsonl(records_path)
    before_total = len(rows)

    # Repair duplicate IDs first: keep the first seen ID, re-id the rest.
    id_seen: set[str] = set()
    reid_count = 0
    for r in rows:
        rid = str(r.get("id") or "").strip()
        if not rid:
            r["id"] = f"mem_{uuid.uuid4().hex}"
            reid_count += 1
            continue
        if rid in id_seen:
            r["id"] = f"mem_{uuid.uuid4().hex}"
            reid_count += 1
        else:
            id_seen.add(rid)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        norm = _norm_text(r)
        if not norm:
            # keep empty records in isolated buckets by id to avoid accidental merges
            norm = f"__empty__:{r.get('id','')}"
        groups[_h(norm)].append(r)

    superseded_count = 0
    cluster_count = 0
    canonical_updated = 0

    for members in groups.values():
        if len(members) <= 1:
            continue
        cluster_count += 1
        members.sort(key=_canonical_sort_key)
        canonical = members[0]
        cid = str(canonical.get("id") or "")
        evidence_ids: list[str] = []
        for m in members:
            mid = str(m.get("id") or "")
            if mid:
                evidence_ids.append(mid)
            if mid == cid:
                continue
            old_status = str(m.get("status") or "accepted")
            if not old_status:
                old_status = "accepted"
                m["status"] = old_status
            if is_valid_status_transition(old_status, "superseded"):
                if str(m.get("status") or "") != "superseded":
                    superseded_count += 1
                m["status"] = "superseded"
                m["superseded_by"] = cid
                m["can_recall"] = False
                m["can_inject"] = False
                m["can_act_on"] = False
                m.setdefault("meta", {})
                if isinstance(m["meta"], dict):
                    m["meta"]["dedupe_superseded_at"] = _now()
        canonical["evidence_count"] = len(evidence_ids)
        canonical["evidence_ids"] = evidence_ids
        canonical.setdefault("meta", {})
        if isinstance(canonical["meta"], dict):
            canonical["meta"]["dedupe_clustered_at"] = _now()
        canonical_updated += 1

    archived_rows: list[dict[str, Any]] = []
    final_rows = rows
    if collapse_superseded:
        kept: list[dict[str, Any]] = []
        for r in rows:
            if str(r.get("status") or "") == "superseded":
                archived_rows.append(r)
            else:
                kept.append(r)
        final_rows = kept

    status_counts = Counter(str(r.get("status") or "") for r in final_rows)
    after_dup_id_groups = sum(1 for _id, c in Counter(str(r.get("id") or "") for r in rows).items() if _id and c > 1)
    if collapse_superseded:
        regroups: dict[str, int] = defaultdict(int)
        for r in final_rows:
            norm = _norm_text(r)
            if not norm:
                norm = f"__empty__:{r.get('id','')}"
            regroups[_h(norm)] += 1
        after_dup_text_groups = sum(1 for _k, c in regroups.items() if c > 1)
    else:
        after_dup_text_groups = sum(1 for _k, members in groups.items() if len(members) > 1)

    report = {
        "at": _now(),
        "dry_run": dry_run,
        "records_file": str(records_path),
        "before_total": before_total,
        "reid_count": reid_count,
        "duplicate_text_clusters": cluster_count,
        "canonical_updated": canonical_updated,
        "superseded_count": superseded_count,
        "collapsed": bool(collapse_superseded),
        "archived_superseded": len(archived_rows),
        "after_duplicate_id_groups": after_dup_id_groups,
        "after_duplicate_text_groups": after_dup_text_groups,
        "status_counts": dict(status_counts),
    }

    if not dry_run:
        _write_jsonl(records_path, final_rows)
        if collapse_superseded and archive_path is not None and archived_rows:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            # Append mode to preserve previous archive history.
            existing = ""
            if archive_path.exists():
                existing = archive_path.read_text(encoding="utf-8", errors="ignore")
            add = "\n".join(json.dumps(r, ensure_ascii=False) for r in archived_rows)
            if add:
                add += "\n"
            archive_path.write_text(existing + add, encoding="utf-8")

    return report


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="One-off strong dedupe for MS8 records")
    p.add_argument("--records", required=True, help="Path to auto_memory_records.jsonl")
    p.add_argument("--report", required=True, help="Output report JSON path")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--collapse-superseded", action="store_true", help="Remove superseded duplicates from active file and archive them")
    p.add_argument("--archive", default="", help="Archive file for collapsed superseded records")
    args = p.parse_args()

    records_path = Path(args.records).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()

    archive_path = Path(args.archive).expanduser().resolve() if str(args.archive).strip() else None
    result = strong_dedupe(
        records_path,
        dry_run=bool(args.dry_run),
        collapse_superseded=bool(args.collapse_superseded),
        archive_path=archive_path,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
