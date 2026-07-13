from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.ports.record_store import HeadMismatchError, LedgerIntegrityError

FIXED_TIME = "2026-07-12T00:00:00+00:00"


def _transaction(sequence: int, prev_hash: str, transaction_id: str) -> LedgerTransaction:
    return LedgerTransaction.create(
        sequence=sequence,
        actor=Actor(kind="system", id="test-suite"),
        events=[
            LedgerEvent(
                type="memory_event.recorded",
                payload={"event_id": f"evt_{sequence}", "content_hash": f"sha256:{sequence}"},
            )
        ],
        prev_hash=prev_hash,
        transaction_id=transaction_id,
        recorded_at=FIXED_TIME,
    )


def test_append_iterate_verify_and_snapshot(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction(1, GENESIS_HASH, "txn_1")
    first_result = store.append(first, expected_head=GENESIS_HASH)
    second = _transaction(2, first.hash, "txn_2")
    second_result = store.append(second, expected_head=first.hash)

    verification = store.verify()
    restored = list(store.iterate())
    snapshot = store.snapshot()

    assert first_result.previous_head == GENESIS_HASH
    assert second_result.new_head == second.hash
    assert verification.valid is True
    assert verification.transaction_count == 2
    assert verification.last_sequence == 2
    assert verification.last_valid_hash == second.hash
    assert restored == [first, second]
    assert list(store.iterate(after_sequence=1)) == [second]
    assert snapshot.ledger_head == second.hash
    assert snapshot.last_sequence == 2
    assert (snapshot.path / "events.jsonl").is_file()
    assert (snapshot.path / "manifest.json").is_file()
    assert (snapshot.path / "snapshot.json").is_file()


def test_expected_head_prevents_silent_overwrite(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction(1, GENESIS_HASH, "txn_first")
    store.append(first, expected_head=GENESIS_HASH)
    stale = _transaction(2, first.hash, "txn_stale")

    with pytest.raises(HeadMismatchError):
        store.append(stale, expected_head=GENESIS_HASH)

    assert store.verify().transaction_count == 1


def test_two_concurrent_writers_cannot_both_advance_same_head(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    candidates = [
        _transaction(1, GENESIS_HASH, "txn_writer_a"),
        _transaction(1, GENESIS_HASH, "txn_writer_b"),
    ]

    def append_candidate(transaction: LedgerTransaction) -> str:
        try:
            store.append(transaction, expected_head=GENESIS_HASH)
        except HeadMismatchError:
            return "head_mismatch"
        return "appended"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append_candidate, candidates))

    assert sorted(results) == ["appended", "head_mismatch"]
    assert store.verify().transaction_count == 1


def test_truncated_tail_is_reported_and_excluded_from_iteration(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction(1, GENESIS_HASH, "txn_1")
    store.append(first)
    with store.ledger_path.open("ab") as handle:
        handle.write(b'{"schema":"ms8.ledger.v1"')

    verification = store.verify()

    assert verification.valid is False
    assert verification.truncated_tail_detected is True
    assert verification.repairable_tail is True
    assert verification.damaged_bytes > 0
    assert verification.transaction_count == 1
    assert list(store.iterate()) == [first]
    with pytest.raises(LedgerIntegrityError):
        store.append(_transaction(2, first.hash, "txn_2"), expected_head=first.hash)


def test_tail_repair_is_dry_run_by_default_and_backed_up_on_apply(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction(1, GENESIS_HASH, "txn_1")
    store.append(first)
    with store.ledger_path.open("ab") as handle:
        handle.write(b'{"schema":"ms8.ledger.v1"')
    damaged_size = store.ledger_path.stat().st_size

    preview = store.repair_tail()

    assert preview.applied is False
    assert preview.repairable is True
    assert preview.removed_bytes > 0
    assert store.ledger_path.stat().st_size == damaged_size

    applied = store.repair_tail(dry_run=False)

    assert applied.applied is True
    assert applied.backup_path is not None
    assert applied.backup_path.is_file()
    assert store.verify().valid is True
    assert list(store.iterate()) == [first]
    second = _transaction(2, first.hash, "txn_2")
    store.append(second, expected_head=first.hash)
    assert store.verify().last_valid_hash == second.hash


def test_non_tail_corruption_is_not_auto_repaired(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction(1, GENESIS_HASH, "txn_1")
    store.append(first)
    second = _transaction(2, first.hash, "txn_2")
    with store.ledger_path.open("ab") as handle:
        handle.write(b'{"invalid":true}\n')
        handle.write((second.to_json_line() + "\n").encode("utf-8"))

    verification = store.verify()

    assert verification.valid is False
    assert verification.repairable_tail is False
    with pytest.raises(LedgerIntegrityError, match="not confined"):
        store.repair_tail(dry_run=False)


def test_hash_broken_transaction_never_enters_valid_prefix(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction(1, GENESIS_HASH, "txn_1")
    store.append(first)
    second = _transaction(2, first.hash, "txn_2")
    payload = second.to_dict()
    payload["events"][0]["payload"]["event_id"] = "tampered"
    with store.ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    verification = store.verify()

    assert verification.valid is False
    assert verification.transaction_count == 1
    assert verification.last_valid_hash == first.hash
    assert list(store.iterate()) == [first]
    assert any("invalid_transaction" in reason for reason in verification.reason_codes)


def test_sequence_gap_never_enters_valid_prefix(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction(1, GENESIS_HASH, "txn_1")
    store.append(first)
    third = _transaction(3, first.hash, "txn_3")
    with store.ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(third.to_json_line() + "\n")

    verification = store.verify()

    assert verification.valid is False
    assert verification.invalid_sequence == 2
    assert verification.invalid_line_number == 2
    assert list(store.iterate()) == [first]
    assert any("sequence_mismatch" in reason for reason in verification.reason_codes)


def test_stale_manifest_is_rebuilt_from_authoritative_ledger(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction(1, GENESIS_HASH, "txn_1")
    store.append(first)
    store.manifest_path.write_text(
        json.dumps(
            {
                "schema": "ms8.ledger.manifest.v1",
                "ledger_schema": "ms8.ledger.v1",
                "head_hash": GENESIS_HASH,
                "last_sequence": 0,
                "transaction_count": 0,
                "updated_at": FIXED_TIME,
            }
        ),
        encoding="utf-8",
    )

    verification = store.verify()
    repaired_manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))

    assert verification.valid is True
    assert repaired_manifest["head_hash"] == first.hash
    assert repaired_manifest["last_sequence"] == 1
    assert repaired_manifest["transaction_count"] == 1


def test_snapshot_export_and_restore_are_verified_and_reversible(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction(1, GENESIS_HASH, "txn_1")
    store.append(first)
    snapshot = store.snapshot()
    exported = store.export_snapshot(snapshot, tmp_path / "exported-snapshot")
    second = _transaction(2, first.hash, "txn_2")
    store.append(second, expected_head=first.hash)

    preview = store.restore_snapshot(exported.path, expected_head=second.hash)

    assert preview.applied is False
    assert preview.previous_head == second.hash
    assert preview.restored_head == first.hash
    assert store.verify().last_sequence == 2

    restored = store.restore_snapshot(exported.path, expected_head=second.hash, dry_run=False)

    assert restored.applied is True
    assert restored.pre_restore_backup is not None
    assert restored.pre_restore_backup.is_dir()
    assert store.verify().valid is True
    assert store.verify().last_sequence == 1
    assert list(store.iterate()) == [first]


def test_restore_rejects_tampered_snapshot(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction(1, GENESIS_HASH, "txn_1")
    store.append(first)
    snapshot = store.snapshot()
    with (snapshot.path / "events.jsonl").open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(LedgerIntegrityError, match="snapshot ledger is invalid"):
        store.restore_snapshot(snapshot.path)
