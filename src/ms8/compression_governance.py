from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .record_policy import is_valid_status_transition


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else "")
    path.write_text(payload, encoding="utf-8")


def _norm(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _hash(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def cluster_duplicate_records(
    *,
    records_file: Path,
    report_file: Path,
    collapse_superseded: bool = False,
    superseded_archive_file: Path | None = None,
) -> dict[str, Any]:
    rows = _read_jsonl(records_file)
    if not rows:
        empty_report: dict[str, Any] = {"at": _utc_now(), "status": "skipped", "reason": "records_empty"}
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(empty_report, ensure_ascii=False, indent=2), encoding="utf-8")
        return empty_report

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        txt = str(row.get("normalized_text") or row.get("text") or "")
        h = _hash(txt)
        groups.setdefault(h, []).append(row)

    updated_rows: list[dict[str, Any]] = []
    clusters = 0
    superseded = 0
    for _, members in groups.items():
        if len(members) == 1:
            updated_rows.append(members[0])
            continue
        clusters += 1
        members_sorted = sorted(
            members,
            key=lambda r: str(r.get("created_at") or ""),
        )
        canonical = members_sorted[0]
        cid = str(canonical.get("id") or "")
        evidence = []
        for index, m in enumerate(members_sorted):
            mid = str(m.get("id") or "")
            evidence.append(mid)
            if index > 0:
                old_status = str(m.get("status", "") or "")
                if is_valid_status_transition(old_status, "superseded"):
                    m["status"] = "superseded"
                    m["superseded_by"] = cid
                    m["can_recall"] = False
                    m["can_inject"] = False
                    superseded += 1
            updated_rows.append(m)
        canonical["evidence_count"] = len(evidence)
        canonical["evidence_ids"] = evidence
        canonical.setdefault("meta", {})
        if isinstance(canonical["meta"], dict):
            canonical["meta"]["duplicate_clustered_at"] = _utc_now()

    archived_rows: list[dict[str, Any]] = []
    active_rows = updated_rows
    if collapse_superseded:
        kept: list[dict[str, Any]] = []
        for row in updated_rows:
            if str(row.get("status") or "") == "superseded":
                archived_rows.append(row)
            else:
                kept.append(row)
        active_rows = kept

    _write_jsonl(records_file, active_rows)
    if collapse_superseded and superseded_archive_file is not None and archived_rows:
        superseded_archive_file.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if superseded_archive_file.exists():
            existing = superseded_archive_file.read_text(encoding="utf-8", errors="ignore")
        add = "\n".join(json.dumps(r, ensure_ascii=False) for r in archived_rows)
        if add:
            add += "\n"
        superseded_archive_file.write_text(existing + add, encoding="utf-8")
    report: dict[str, Any] = {
        "at": _utc_now(),
        "status": "success",
        "total_records": len(rows),
        "duplicate_clusters": clusters,
        "superseded_records": superseded,
        "collapsed": bool(collapse_superseded),
        "archived_superseded": len(archived_rows),
        "records_file": str(records_file),
    }
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
