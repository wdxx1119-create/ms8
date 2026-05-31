from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.security.shadow.shadow_quarantine import ShadowQuarantine, grade_reason
from ms8.engine_core.security.shadow.shadow_recovery import ShadowRecovery


class _DummyLedger:
    def __init__(self, rows=None, scanned=None):
        self._rows = list(rows or [])
        self._scanned = list(scanned or [])
        self.rewritten = None

    def read_spool(self):
        return list(self._rows)

    def rewrite_spool(self, rows):
        self.rewritten = list(rows)


class _DummyGuard:
    TARGETS = {"main_memory", "quarantine_memory", "drill_memory"}

    def __init__(self, scanned=None, admit=None, quarantine=None, skipped=None, apply_result=None):
        self._scanned = list(scanned or [])
        self._admit = list(admit or [])
        self._quarantine = list(quarantine or [])
        self._skipped = list(skipped or [])
        self._apply_result = dict(apply_result or {"replayed": 0, "skipped": 0, "failed": 0})
        self.quarantine_called = 0

    def scan(self, since_ts: str, include_spool: bool):
        return list(self._scanned)

    def decide(self, rows, target: str):
        return list(self._admit), list(self._quarantine), list(self._skipped)

    def apply(self, **kwargs):
        return dict(self._apply_result)

    def quarantine(self, rows, reason: str):
        self.quarantine_called += 1
        return {"status": "success"}


class _DummySeal:
    def __init__(self):
        self.marked = 0
        self.cleared = 0
        self.triggered = 0

    def mark_recovering(self):
        self.marked += 1

    def clear_seal(self, reason: str):
        self.cleared += 1
        return {"status": "ok"}

    def trigger_seal(self, reason: str, level: str):
        self.triggered += 1
        return {"status": "ok"}

    def seal_level(self):
        return "hard"


def _mk_rec(event_id: str, state: str = "pending", failure: str = ""):
    from ms8.engine_core.security.shadow.shadow_recovery_guard import RecoveryRecord

    return RecoveryRecord(
        event_id=event_id,
        source="shadow:test",
        text="hello",
        content_hash=f"h-{event_id}",
        ts="2026-05-19T00:00:00Z",
        mode="sealed",
        origin="spool",
        replay_state=state,
        failure_reason=failure,
    )


def test_grade_reason_levels():
    assert grade_reason("signature invalid") == "high"
    assert grade_reason("payload_too_large") == "medium"
    assert grade_reason("other reason") == "low"


def test_quarantine_append_writes_row(tmp_path: Path):
    q = ShadowQuarantine(tmp_path)
    out = q.append({"k": "v"}, reason="tamper_detected")
    assert out["status"] == "success"
    assert out["severity"] == "high"
    p = Path(out["file"])
    assert p.exists()
    row = json.loads(p.read_text(encoding="utf-8").strip().splitlines()[0])
    assert row["k"] == "v"
    assert row["quarantine_reason"] == "tamper_detected"
    assert row["quarantine_severity"] == "high"


def test_replay_spool_rejects_invalid_target():
    rec = _mk_rec("s1")
    guard = _DummyGuard(scanned=[rec], admit=[rec])
    recovery = ShadowRecovery(ledger=_DummyLedger(rows=[]), seal=_DummySeal(), guard=guard)
    out = recovery.replay_spool(target="unknown")
    assert out["status"] == "rejected"
    assert out["reason"] == "invalid_recovery_target"


def test_replay_spool_rejects_unbound_target():
    rec = _mk_rec("s1")
    guard = _DummyGuard(scanned=[rec], admit=[rec])
    recovery = ShadowRecovery(ledger=_DummyLedger(rows=[]), seal=_DummySeal(), guard=guard)
    out = recovery.replay_spool(target="main_memory")
    assert out["status"] == "rejected"
    assert out["reason"] == "target_not_bound"


def test_replay_spool_success_updates_rows_and_clears_seal():
    rows = [{"spool_id": "s1", "replayed": False}]
    rec = _mk_rec("s1", state="replayed")
    guard = _DummyGuard(scanned=[rec], admit=[rec], apply_result={"replayed": 1, "skipped": 0, "failed": 0})
    seal = _DummySeal()
    ledger = _DummyLedger(rows=rows)
    recovery = ShadowRecovery(ledger=ledger, seal=seal, guard=guard)
    recovery.bind_target("main_memory", lambda *args, **kwargs: None, hash_exists_func=lambda _: False)
    out = recovery.replay_spool(target="main_memory")
    assert out["status"] == "success"
    assert out["replayed"] == 1
    assert out["failed"] == 0
    assert seal.marked == 1
    assert seal.cleared == 1
    assert seal.triggered == 0
    assert ledger.rewritten and ledger.rewritten[0]["replayed"] is True


def test_replay_spool_partial_triggers_seal_and_quarantine():
    rows = [{"spool_id": "s1", "replayed": False}, {"spool_id": "s2", "replayed": False}]
    admit = [_mk_rec("s1", state="failed", failure="err")]
    quarantine = [_mk_rec("s2", state="quarantine", failure="bad")]
    guard = _DummyGuard(
        scanned=admit + quarantine,
        admit=admit,
        quarantine=quarantine,
        apply_result={"replayed": 0, "skipped": 0, "failed": 1},
    )
    seal = _DummySeal()
    ledger = _DummyLedger(rows=rows)
    recovery = ShadowRecovery(ledger=ledger, seal=seal, guard=guard)
    recovery.bind_target("main_memory", lambda *args, **kwargs: None, hash_exists_func=lambda _: False)
    out = recovery.replay_spool(target="main_memory")
    assert out["status"] == "partial"
    assert out["failed"] == 1
    assert out["quarantined"] == 1
    assert guard.quarantine_called == 1
    assert seal.triggered == 1
    assert seal.cleared == 0


def test_recover_from_events_rejected_when_not_bound():
    rec = _mk_rec("e1")
    guard = _DummyGuard(scanned=[rec], admit=[rec])
    recovery = ShadowRecovery(ledger=_DummyLedger(scanned=[]), seal=_DummySeal(), guard=guard)
    out = recovery.recover_from_events(target="main_memory")
    assert out["status"] == "rejected"
    assert out["reason"] == "target_not_bound"


def test_recover_from_events_success():
    rec = _mk_rec("e1")
    guard = _DummyGuard(scanned=[rec], admit=[rec], apply_result={"replayed": 1, "skipped": 0, "failed": 0})
    seal = _DummySeal()
    recovery = ShadowRecovery(ledger=_DummyLedger(scanned=[]), seal=seal, guard=guard)
    recovery.bind_target("main_memory", lambda *args, **kwargs: None, hash_exists_func=lambda _: False)
    out = recovery.recover_from_events(target="main_memory")
    assert out["status"] == "success"
    assert out["recovered"] == 1
    assert out["failed"] == 0
    assert seal.cleared == 1
