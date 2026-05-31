from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine_core.security.shadow.shadow_ledger import ShadowLedger


def test_append_event_and_read_events(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "shadow", checkpoint_interval=2, snapshot_interval=1000)
    row1 = ledger.append_event(
        event_type="memory",
        action="write",
        source="unit",
        mode="normal",
        ok=True,
        content="hello",
    )
    row2 = ledger.append_event(
        event_type="memory",
        action="write",
        source="unit",
        mode="normal",
        ok=True,
        content="world",
    )
    assert row1["seq"] == 1
    assert row2["seq"] == 2
    rows = list(ledger.read_events())
    assert len(rows) == 2
    assert ledger.checkpoints_file.exists() is True


def test_spool_encrypt_decrypt_and_rewrite(tmp_path: Path) -> None:
    enc = lambda s: f"enc::{s}"  # noqa: E731
    dec = lambda s: s.replace("enc::", "", 1) if s.startswith("enc::") else s  # noqa: E731
    ledger = ShadowLedger(
        tmp_path / "shadow",
        spool_encryptor=enc,
        spool_decryptor=dec,
        spool_encryption_enabled=True,
    )
    item = ledger.append_spool("src", "payload")
    assert item["content_encrypted"] is True
    rows = ledger.read_spool()
    assert rows[0]["content"] == "payload"
    rows[0]["replayed"] = True
    ledger.rewrite_spool(rows)
    reread = ledger.read_spool()
    assert reread[0]["content"] == "payload"
    assert reread[0]["replayed"] is True


def test_archive_replayed_spool_and_empty_skip(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "shadow")
    assert ledger.archive_replayed_spool()["status"] == "skipped"
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    rows = [
        {"spool_id": "s1", "ts": old, "replayed_at": old, "replayed": True, "content": "a"},
        {"spool_id": "s2", "ts": old, "replayed_at": old, "replayed": False, "content": "b"},
    ]
    ledger.rewrite_spool(rows)
    out = ledger.archive_replayed_spool(hot_days=1, warm_days=7, cold_days=30)
    assert out["status"] == "success"
    assert out["archived"] == 1
    assert out["kept"] == 1


def test_startup_self_heal_repairs_corrupt_jsonl(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    ledger = ShadowLedger(shadow)
    shadow.mkdir(parents=True, exist_ok=True)
    ledger.events_file.write_text('{"seq":1}\n{bad\n', encoding="utf-8")
    ledger.spool_file.write_text("{bad\n", encoding="utf-8")
    out = ledger.startup_self_heal()
    assert out["status"] == "success"
    assert out["corrupt_lines_total"] >= 1
    assert any(r.get("status") == "repaired" for r in out["reports"])


def test_rotate_events_monthly_and_snapshot_verify(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "shadow")
    now = datetime.now(timezone.utc)
    prev = (now - timedelta(days=35)).isoformat()
    cur = now.isoformat()
    lines = [
        json.dumps({"seq": 1, "ts": prev, "action": "a"}),
        json.dumps({"seq": 2, "ts": cur, "action": "b"}),
        "{bad-json",
    ]
    ledger.events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = ledger.rotate_events_monthly()
    assert out["status"] == "success"
    assert out["kept"] >= 2
    events_dir = ledger.archive_dir / "events"
    assert any(events_dir.glob("shadow_events.*.jsonl.gz"))
    # snapshot verify
    snap = tmp_path / "snap.jsonl"
    snap.write_text('{"a":1}\n', encoding="utf-8")
    assert ledger.verify_snapshot(str(snap))["ok"] is True
    assert ledger.verify_snapshot(str(tmp_path / "none.jsonl"))["ok"] is False


def test_verify_checkpoints_and_rebuild(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "shadow", checkpoint_interval=2)
    # no checkpoints
    out0 = ledger.verify_checkpoints()
    assert out0["ok"] is True
    # append 4 events to emit checkpoints at seq2/seq4
    for i in range(4):
        ledger.append_event(
            event_type="m",
            action=f"a{i}",
            source="u",
            mode="n",
            ok=True,
            content=f"c{i}",
        )
    out1 = ledger.verify_checkpoints()
    assert out1["status"] in {"ok", "mismatch"}
    rebuild = ledger.rebuild_checkpoints_from_events(interval=2)
    assert rebuild["status"] == "success"
    out2 = ledger.verify_checkpoints()
    assert out2["ok"] is True

