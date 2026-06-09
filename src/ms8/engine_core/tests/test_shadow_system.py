from __future__ import annotations

import gzip
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ms8.engine_core.security.shadow import get_shadow_system
from ms8.engine_core.security.shadow import shadow_guard as shadow_guard_mod


@pytest.fixture(autouse=True)
def _isolate_shadow_singletons():
    shadow_guard_mod._SHADOW_SINGLETONS.clear()
    yield
    shadow_guard_mod._SHADOW_SINGLETONS.clear()


def _cfg(base: Path) -> dict:
    return {
        "memory_dir": base,
        "settings": {
            "memory": {
                "security": {
                    "shadow": {
                        "enabled": True,
                        "shadow_dir": str(base / "security" / "shadow_data"),
                        "payload_threshold_chars": 16,
                        "checkpoint_interval": 3,
                        "snapshot_interval": 3,
                        "snapshot_keep": 2,
                    }
                }
            }
        },
    }


def test_checkpoint_hash_every_interval() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_case_"))
    shadow = get_shadow_system(_cfg(base))
    for i in range(4):
        shadow.record_data(action="write", source="test", content=f"hello-{i}", ok=True)
    cp = base / "security" / "shadow_data" / "shadow_checkpoints.jsonl"
    assert cp.exists()
    lines = [x for x in cp.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) >= 1


def test_spool_replay_failed_rows_are_kept() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_case_"))
    shadow = get_shadow_system(_cfg(base))
    shadow.trigger_seal("unit")
    shadow.spool_write("A", "test")
    shadow.spool_write("B", "test")

    def flaky_write(text: str, source: str, _meta: dict) -> None:
        if text == "B":
            raise RuntimeError("intentional")

    shadow.bind_recovery_target("main_memory", flaky_write)
    first = shadow.replay_spool(target="main_memory")
    assert first["failed"] == 1
    assert first["remaining"] == 1

    shadow.bind_recovery_target("main_memory", lambda _t, _s, _m: None)
    second = shadow.replay_spool(target="main_memory")
    assert second["failed"] == 0
    assert second["remaining"] == 0


def test_soft_vs_hard_seal_takeover_policy() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_case_"))
    shadow = get_shadow_system(_cfg(base))

    # soft seal: only high risk should be diverted
    shadow.trigger_seal("unit-soft", level="soft")
    assert shadow.is_sealed() is True
    assert shadow.status().get("seal_level") == "soft"
    assert shadow.should_takeover_write("high") is True
    assert shadow.should_takeover_write("low") is False

    # promote to hard without unseal
    shadow.trigger_seal("unit-hard", level="hard")
    assert shadow.status().get("seal_level") == "hard"
    assert shadow.should_takeover_write("high") is True
    assert shadow.should_takeover_write("low") is True


def test_verify_checkpoints_ok() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_case_"))
    shadow = get_shadow_system(_cfg(base))
    for i in range(6):
        shadow.record_data(action="write", source="test", content=f"verify-{i}", ok=True)
    result = shadow.verify_checkpoints()
    assert result["ok"] is True


def test_replay_batch_id_and_dedupe_key() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_case_"))
    shadow = get_shadow_system(_cfg(base))
    shadow.trigger_seal("unit")
    shadow.spool_write("same-content", "tool:a")
    shadow.spool_write("same-content", "tool:a")

    calls = {"n": 0}

    def writer(_text: str, _source: str, _meta: dict) -> None:
        calls["n"] += 1

    shadow.bind_recovery_target("main_memory", writer)
    out = shadow.replay_spool(target="main_memory")
    assert str(out.get("batch_id", "")).startswith("replay-")
    assert calls["n"] == 1
    assert int(out.get("replayed", 0)) == 1
    assert int(out.get("skipped", 0)) >= 1


def test_startup_self_heal_repairs_corrupt_spool_lines() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_case_"))
    shadow = get_shadow_system(_cfg(base))
    spool = base / "security" / "shadow_data" / "shadow_spool.jsonl"
    spool.parent.mkdir(parents=True, exist_ok=True)
    spool.write_text(
        '{"spool_id":"a","replayed":false,"content":"ok"}\n{bad-json-line\n',
        encoding="utf-8",
    )
    report = shadow.startup_self_heal()
    assert report["status"] == "success"
    assert int(report.get("corrupt_lines_total", 0)) >= 1


def test_rotate_events_monthly_archives_old_rows() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_rotate_"))
    shadow = get_shadow_system(_cfg(base))
    events = base / "security" / "shadow_data" / "shadow_events.jsonl"
    now = datetime.now(timezone.utc)
    cur_ts = now.isoformat()
    old_ts = datetime(now.year - 1, now.month, 1, tzinfo=timezone.utc).isoformat()
    row_old = {
        "event_id": "old-1",
        "seq": 1,
        "ts": old_ts,
        "event_type": "data",
        "action": "write",
        "source": "test",
        "mode": "active",
        "ok": True,
        "summary": "old",
    }
    row_cur = {
        "event_id": "cur-1",
        "seq": 2,
        "ts": cur_ts,
        "event_type": "data",
        "action": "write",
        "source": "test",
        "mode": "active",
        "ok": True,
        "summary": "cur",
    }
    events.write_text(
        json.dumps(row_old, ensure_ascii=False) + "\n" + json.dumps(row_cur, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out = shadow.rotate_events_monthly()
    assert out["status"] == "success"
    assert int(out.get("archived", 0)) >= 1
    gz_files = list((base / "security" / "shadow_data" / "archive" / "events").glob("shadow_events.*.jsonl.gz"))
    assert gz_files
    with gzip.open(gz_files[0], "rt", encoding="utf-8") as f:
        txt = f.read()
    assert "old-1" in txt


def test_recovery_drill_entrypoint_runs() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_drill_"))
    shadow = get_shadow_system(_cfg(base))
    out = shadow.run_recovery_drill(caller_id="trusted_cli", sample_text="drill-sample")
    assert str(out.get("status")) in {"success", "partial"}
    replay = out.get("replay", {}) if isinstance(out.get("replay", {}), dict) else {}
    assert int(replay.get("replayed", 0) or 0) >= 1


def test_startup_integrity_emit_throttled() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_startup_emit_"))
    cfg = _cfg(base)
    cfg["settings"]["memory"]["security"]["shadow"]["startup_integrity_emit_cooldown_seconds"] = 3600
    shadow = get_shadow_system(cfg)
    events = base / "security" / "shadow_data" / "shadow_events.jsonl"
    before = 0
    if events.exists():
        for line in events.read_text(encoding="utf-8", errors="ignore").splitlines():
            if '"source":"shadow:startup_integrity"' in line:
                before += 1
    shadow._startup_integrity_scan()
    shadow._startup_integrity_scan()
    after = 0
    for line in events.read_text(encoding="utf-8", errors="ignore").splitlines():
        if '"source":"shadow:startup_integrity"' in line:
            after += 1
    # Within cooldown with same signature, additional scans should not keep appending.
    assert after == before
