from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.security.shadow.shadow_recovery_guard import RecoveryRecord, ShadowRecoveryGuard


class _LedgerForGuard:
    def __init__(self, events=None, spool=None, payload_dir: Path | None = None):
        self._events = list(events or [])
        self._spool = list(spool or [])
        self.payload_dir = payload_dir or Path(".")

    def read_events(self):
        return list(self._events)

    def read_spool(self):
        return list(self._spool)


def test_scan_collects_events_and_spool(tmp_path: Path):
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    (payload_dir / "p1.json").write_text(json.dumps({"content": "payload body"}), encoding="utf-8")

    events = [
        {
            "event_id": "e1",
            "event_type": "data",
            "action": "write",
            "mode": "sealed",
            "ts": "2026-05-19T00:00:00Z",
            "summary": "fallback summary",
            "payload_file": "p1.json",
            "source": "shadow:event",
            "content_hash": "h1",
        },
        {"event_id": "e2", "event_type": "data", "action": "write", "mode": "active", "ts": "2026-05-19T00:00:00Z"},
    ]
    spool = [{"spool_id": "s1", "source": "shadow:spool", "content": "spool body", "content_hash": "h2", "ts": "2026-05-19T00:00:01Z"}]
    guard = ShadowRecoveryGuard(_LedgerForGuard(events=events, spool=spool, payload_dir=payload_dir), tmp_path)
    rows = guard.scan(include_spool=True)
    assert len(rows) == 2
    by_id = {r.event_id: r for r in rows}
    assert by_id["e1"].text == "payload body"
    assert by_id["s1"].origin == "spool"


def test_decide_routes_skip_quarantine_and_admit(tmp_path: Path):
    def admission_check(text: str, meta: dict):
        if text == "reject me":
            return {"route": "rejected"}
        return {"route": "accepted"}

    guard = ShadowRecoveryGuard(_LedgerForGuard(), tmp_path, admission_check=admission_check)
    huge_text = "x" * (1024 * 100 + 10)
    rows = [
        RecoveryRecord("a", "shadow:test", "", "h-empty", "2026-05-19T00:00:00Z", "sealed", "events"),
        RecoveryRecord("b", "shadow:test", huge_text, "h-huge", "2026-05-19T00:00:00Z", "sealed", "events"),
        RecoveryRecord("c", "shadow:test", "dup1", "h-dup", "2026-05-19T00:00:00Z", "sealed", "events"),
        RecoveryRecord("d", "shadow:test", "dup2", "h-dup", "2026-05-19T00:00:00Z", "sealed", "events"),
        RecoveryRecord("e", "shadow:test", "reject me", "h-r", "2026-05-19T00:00:00Z", "sealed", "events"),
        RecoveryRecord("f", "shadow:test", "accept me", "h-a", "2026-05-19T00:00:00Z", "sealed", "events"),
    ]
    admit, quarantine, skip = guard.decide(rows, target="main_memory")
    assert [r.event_id for r in admit] == ["c", "f"]
    assert {r.event_id for r in quarantine} == {"b", "e"}
    assert {r.event_id for r in skip} == {"a", "d"}


def test_apply_handles_exists_runtimeerror_and_write_failure(tmp_path: Path):
    guard = ShadowRecoveryGuard(_LedgerForGuard(), tmp_path)
    rows = [
        RecoveryRecord("a", "user", "a", "h-a", "2026-05-19T00:00:00Z", "sealed", "events"),
        RecoveryRecord("b", "user", "b", "h-b", "2026-05-19T00:00:00Z", "sealed", "events"),
        RecoveryRecord("c", "user", "c", "h-c", "2026-05-19T00:00:00Z", "sealed", "events"),
    ]

    def exists_func(h: str):
        if h == "h-b":
            raise RuntimeError("probe fail")
        return h == "h-a"

    def write_target(text: str, source: str, meta: dict):
        if text == "c":
            raise ValueError("write failed")
        return {"ok": True}

    out = guard.apply(
        batch_id="b1",
        rows=rows,
        write_target=write_target,
        hash_exists_func=exists_func,
        allow_source_prefix="shadow:",
    )
    assert out["status"] == "partial"
    assert out["total"] == 3
    assert out["skipped"] == 1
    assert out["replayed"] == 1
    assert out["failed"] == 1
    journal = (tmp_path / "recovery_batches.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(journal) >= 1


def test_quarantine_writes_entries(tmp_path: Path):
    guard = ShadowRecoveryGuard(_LedgerForGuard(), tmp_path)
    rows = [
        RecoveryRecord("q1", "shadow:test", "x", "h1", "2026-05-19T00:00:00Z", "sealed", "events"),
        RecoveryRecord("q2", "shadow:test", "y", "h2", "2026-05-19T00:00:00Z", "sealed", "events"),
    ]
    out = guard.quarantine(rows, reason="admission_rejected")
    assert out["status"] == "success"
    assert out["quarantined"] == 2
    assert out["file"]
