from __future__ import annotations

import inspect
from pathlib import Path

from ms8.engine_core.security.shadow.shadow_seal import ShadowSeal


def test_shadow_seal_unauthorized_calls_are_rejected(tmp_path: Path) -> None:
    seal = ShadowSeal(tmp_path)
    out = seal.trigger_seal("x")
    assert out.get("rejected") is True
    assert out.get("reason") == "seal_call_not_authorized"

    out2 = seal.clear_seal("manual")
    assert out2.get("rejected") is True
    assert out2.get("reason") == "clear_seal_call_not_authorized"


def test_shadow_seal_state_transitions_with_authorized_mode(tmp_path: Path) -> None:
    seal = ShadowSeal(tmp_path)
    seal._authorized = lambda: True  # type: ignore[method-assign]

    st0 = seal.status()
    assert st0["sealed"] is False
    assert st0["mode"] == "active"

    st1 = seal.trigger_seal("unit-test", level="soft")
    assert st1["sealed"] is True
    assert st1["seal_level"] == "soft"
    assert seal.is_sealed() is True
    assert seal.seal_level() == "soft"

    # update existing seal and promote to hard
    st2 = seal.trigger_seal("promote", level="hard")
    assert st2["sealed"] is True
    assert st2["seal_level"] == "hard"

    # recovering state
    seal.mark_recovering()
    assert seal.mode() in {"recovering", "sealed"}

    # minimal survival transitions
    st3 = seal.enter_minimal_survival("capacity_guard")
    assert st3["mode"] == "minimal_survival"
    assert st3["sealed"] is True
    st4 = seal.exit_minimal_survival("manual")
    assert st4["mode"] in {"active", "sealed"}

    # write error streak behavior
    seal.trigger_seal("again", level="soft")
    promoted = seal.note_write_error(threshold=1, reason="io_error")
    assert promoted.get("threshold") == 1
    assert promoted.get("promoted_to_hard") in {True, False}
    seal.note_write_success()

    # unseal
    st5 = seal.clear_seal("recovered")
    assert st5["sealed"] is False
    assert st5["mode"] == "active"


def test_shadow_seal_snapshot_helpers(tmp_path: Path) -> None:
    seal = ShadowSeal(tmp_path)
    # missing snapshot path should return a structured result
    out = seal.restore_manifest_snapshot(str(tmp_path / "missing.json"))
    assert isinstance(out, dict)
    assert out.get("status") in {"error", "missing", "failed", "success"}

    listed = seal.list_manifest_snapshots(limit=5)
    assert isinstance(listed, list)


def test_shadow_seal_load_signature_invalid_and_compact_history(tmp_path: Path, monkeypatch) -> None:
    # signature invalid path => security-first sealed manifest
    seal = ShadowSeal(tmp_path)
    seal.manifest_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        seal.manifest_guard,
        "read_manifest",
        lambda _path: ({}, False, "sig bad"),
    )
    loaded = seal._load()
    assert loaded.sealed is True
    assert loaded.mode == "sealed"
    assert loaded.reason in {"sig bad", "manifest_signature_invalid"}
    assert any(str(x.get("event")) == "manifest_signature_invalid" for x in loaded.history)

    # compact history with repeated seal_update aggregation and truncation summary
    seal._manifest.history = [
        {"ts": "1", "event": "seal_update", "reason": "r", "seal_level": "hard", "seal_session_id": "s"},
        {"ts": "2", "event": "seal_update", "reason": "r", "seal_level": "hard", "seal_session_id": "s"},
        {"ts": "3", "event": "seal_update", "reason": "x", "seal_level": "hard", "seal_session_id": "s"},
        {"ts": "4", "event": "seal_update", "reason": "x", "seal_level": "hard", "seal_session_id": "s"},
    ]
    changed = seal._compact_history(max_keep=1)
    assert changed is True
    assert seal._manifest.history[0]["event"] in {"history_compacted", "seal_update"}


def test_shadow_seal_authorized_stack_error_and_error_streak_branches(tmp_path: Path, monkeypatch) -> None:
    seal = ShadowSeal(tmp_path)

    # _authorized exception branch
    monkeypatch.setattr(inspect, "stack", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert seal._authorized() is False

    # write-error promotion path and note_write_success reset path
    seal._authorized = lambda: True  # type: ignore[method-assign]
    seal.trigger_seal("unit", level="soft")
    out = seal.note_write_error(threshold=1, reason="io")
    assert out["promoted_to_hard"] is True
    assert out["threshold"] == 1
    seal.note_write_success()
    assert seal.status()["write_error_streak"] == 0
