#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def main() -> int:
    from ms8.record_policy import validate_record
    from ms8.runtime import ensure_runtime_dirs, set_maintenance_window

    paths = ensure_runtime_dirs()
    root = paths["root"]
    mem_dir = root / "memory"
    snap_dir = root / "backups" / f"p0_snapshot_{_now()}"
    snap_dir.mkdir(parents=True, exist_ok=True)

    set_maintenance_window(
        True,
        reason="P0-0 emergency stabilization",
        pause_session_ingestion=True,
        pause_maintenance_writes=True,
        pause_review_writes=True,
        pause_compression_writes=True,
    )

    targets = [
        mem_dir / "auto_memory_records.jsonl",
        mem_dir / "auto_memory_index.json",
        mem_dir / "knowledge_graph.db",
        mem_dir / "auto_memory_review_queue.jsonl",
        root / "MEMORY.md",
        mem_dir / "health_report_latest.json",
    ]
    copied = []
    for t in targets:
        if t.exists():
            out = snap_dir / t.name
            shutil.copy2(t, out)
            copied.append(str(out))

    records = _load_jsonl(mem_dir / "auto_memory_records.jsonl")
    non_canonical = 0
    hash_counter: Counter[str] = Counter()
    pending_review = 0
    for row in records:
        ok, _ = validate_record(row)
        if not ok:
            non_canonical += 1
        txt = str(row.get("normalized_text") or row.get("text") or "").strip()
        src = str(row.get("source") or "")
        if txt:
            hash_counter[f"{src}|{txt}"] += 1
        if str(row.get("status", "")) == "pending_review":
            pending_review += 1
    duplicate_groups = sum(1 for _, c in hash_counter.items() if c > 1)

    self_check = {"status": "unknown"}
    stale_hours = None
    try:
        from ms8.runtime import get_engine_monitoring_status, run_engine_self_check

        self_check = run_engine_self_check(level="L4")
        mon = get_engine_monitoring_status()
        if isinstance(mon, dict):
            freshness = mon.get("compression_freshness", {})
            if isinstance(freshness, dict):
                hrs = freshness.get("hours_since_last")
                if isinstance(hrs, (int, float)):
                    stale_hours = float(hrs)
    except Exception:
        pass

    snapshot = {
        "at": _now(),
        "runtime_root": str(root),
        "snapshot_dir": str(snap_dir),
        "copied_files": copied,
        "stats": {
            "record_count": len(records),
            "non_canonical_count": non_canonical,
            "duplicate_group_count": duplicate_groups,
            "pending_review_count": pending_review,
            "self_check_status": str(self_check.get("status", "unknown")) if isinstance(self_check, dict) else "unknown",
            "compression_stale_hours": stale_hours,
        },
    }
    out = snap_dir / "before_snapshot.json"
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
