from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...file_write_guard import atomic_write_json
from .repair_schema import RepairExecutionRow, utc_now_iso


def _paths(memory_dir: Path) -> dict[str, Path]:
    reports = memory_dir / "reports"
    hist = reports / "repair_history"
    logs = memory_dir / "logs"
    states = memory_dir / "state"
    snaps = states / "repair_snapshots"
    for p in (reports, hist, logs, states, snaps):
        p.mkdir(parents=True, exist_ok=True)
    return {
        "audit": logs / "repair_ops_audit.jsonl",
        "latest": reports / "repair_latest.json",
        "history_dir": hist,
        "snapshots": snaps,
    }


def append_repair_audit(memory_dir: Path, row: RepairExecutionRow) -> None:
    p = _paths(memory_dir)["audit"]
    payload = row.to_dict()
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def save_repair_report(memory_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
    ps = _paths(memory_dir)
    latest = ps["latest"]
    stamp = utc_now_iso().replace(":", "").replace("-", "").replace("+00:00", "Z")
    hist = ps["history_dir"] / f"repair-{stamp}.json"
    atomic_write_json(latest, report, ensure_ascii=False, indent=2)
    atomic_write_json(hist, report, ensure_ascii=False, indent=2)
    return {"latest": str(latest), "history": str(hist)}


def load_latest_repair_report(memory_dir: Path) -> dict[str, Any]:
    latest = _paths(memory_dir)["latest"]
    if not latest.exists():
        return {"status": "missing", "path": str(latest)}
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"status": "error", "error": str(exc), "path": str(latest)}


def list_repair_history(memory_dir: Path, limit: int = 10) -> list[dict[str, Any]]:
    hist = _paths(memory_dir)["history_dir"]
    out: list[dict[str, Any]] = []
    for p in sorted(hist.glob("repair-*.json"), reverse=True):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        summary = obj.get("summary", {}) if isinstance(obj.get("summary", {}), dict) else {}
        out.append(
            {
                "path": str(p),
                "started_at": obj.get("started_at", ""),
                "mode": obj.get("mode", ""),
                "status": obj.get("status", ""),
                "success": int(summary.get("success", 0) or 0),
                "failed": int(summary.get("failed", 0) or 0),
                "rolled_back": int(summary.get("rolled_back", 0) or 0),
                "needs_manual": int(summary.get("needs_manual", 0) or 0),
            }
        )
        if len(out) >= max(1, int(limit)):
            break
    return out


def _parse_ts(ts: str) -> datetime | None:
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def summarize_repair_7d(memory_dir: Path, days: int = 7) -> dict[str, Any]:
    ps = _paths(memory_dir)
    audit = ps["audit"]
    if not audit.exists():
        return {
            "window_days": max(1, int(days)),
            "total": 0,
            "success": 0,
            "failed": 0,
            "rolled_back": 0,
            "needs_manual": 0,
            "success_rate": 0.0,
            "rollback_rate": 0.0,
            "manual_rate": 0.0,
        }

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, int(days)))
    total = 0
    success = 0
    failed = 0
    rolled_back = 0
    needs_manual = 0

    lines = audit.read_text(encoding="utf-8", errors="ignore").splitlines()
    for ln in lines[-20000:]:
        raw = ln.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(str(row.get("timestamp", "") or ""))
        if ts is None or ts < cutoff:
            continue
        total += 1
        result = str(row.get("result", "") or "")
        if result == "success":
            success += 1
        if result in {"error", "failed_verify", "blocked"}:
            failed += 1
        if bool(row.get("rolled_back", False)):
            rolled_back += 1
        if result in {"error", "failed_verify"}:
            needs_manual += 1

    def _rate(num: int, den: int) -> float:
        return round((float(num) / float(den)) if den > 0 else 0.0, 4)

    return {
        "window_days": max(1, int(days)),
        "total": total,
        "success": success,
        "failed": failed,
        "rolled_back": rolled_back,
        "needs_manual": needs_manual,
        "success_rate": _rate(success, total),
        "rollback_rate": _rate(rolled_back, total),
        "manual_rate": _rate(needs_manual, total),
    }
