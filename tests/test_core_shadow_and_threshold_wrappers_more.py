from __future__ import annotations

import collections
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core import core as core_mod
from ms8.engine_core.core import MemoryCore


def _core_stub(tmp_path: Path) -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c._recent_query_tokens = collections.deque(maxlen=24)
    c._utc_now = lambda: datetime(2026, 5, 25, tzinfo=timezone.utc)  # type: ignore[method-assign]
    c.config = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {"memory": {"maintenance_policy": {}}},
    }
    c.file_store = SimpleNamespace(
        read_memory_md=lambda: "ROOT",
        write_memory_md=lambda _txt: None,
        append_to_daily_log=lambda _txt: None,
    )
    c.whoosh_search = SimpleNamespace(reindex_all=lambda: None)
    c.crypto = SimpleNamespace(is_enabled=lambda: False, is_unlocked=lambda: True)
    return c


def test_shadow_methods_return_disabled_without_shadow(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c.shadow = None
    assert c.shadow_health()["enabled"] is False
    assert c.shadow_replay_spool()["status"] == "disabled"
    assert c.shadow_archive_spool()["status"] == "disabled"
    assert c.shadow_startup_self_heal()["status"] == "disabled"
    assert c.shadow_rotate_events_monthly()["status"] == "disabled"
    assert c.shadow_sync_verified_backup()["status"] == "disabled"
    assert c.shadow_recover_from_events()["status"] == "disabled"
    assert c.shadow_verify()["status"] == "disabled"
    assert c.shadow_reset_checkpoint()["status"] == "disabled"
    assert c.shadow_restore_snapshot("x")["status"] == "disabled"
    assert c.shadow_list_manifest_snapshots()["status"] == "disabled"
    assert c.shadow_restore_manifest_snapshot("x")["status"] == "disabled"
    assert c.shadow_restore_backup_snapshot("x")["status"] == "disabled"
    assert c.shadow_recovery_drill()["status"] == "disabled"
    assert c.shadow_issue_token("caller", ["read"])["status"] == "disabled"
    assert c.shadow_revoke_token("tok")["status"] == "disabled"
    assert c.shadow_seal()["enabled"] is False
    assert c.shadow_unseal()["enabled"] is False


def test_shadow_replay_and_recover_block_when_crypto_locked(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c.crypto = SimpleNamespace(is_enabled=lambda: True, is_unlocked=lambda: False)
    c.shadow = SimpleNamespace()
    blocked_replay = c.shadow_replay_spool()
    blocked_recover = c.shadow_recover_from_events(since_ts="2026-01-01")
    assert blocked_replay["status"] == "blocked"
    assert blocked_recover["status"] == "blocked"


def test_shadow_reset_checkpoint_error_wrapper(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c.shadow = SimpleNamespace(reset_checkpoint=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    out = c.shadow_reset_checkpoint()
    assert out["status"] == "error"
    assert "boom" in out["error"]


def test_shadow_replay_binds_target_and_calls_shadow(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    bound: dict[str, object] = {}

    def _bind(name: str, write_func, hash_exists) -> None:  # noqa: ANN001
        bound["name"] = name
        bound["write_func"] = write_func
        bound["hash_exists"] = hash_exists

    c.shadow = SimpleNamespace(
        bind_recovery_target=_bind,
        replay_spool=lambda target, caller_id, request_token: {  # noqa: ANN001
            "status": "ok",
            "target": target,
            "caller_id": caller_id,
            "request_token": request_token,
        },
    )
    out = c.shadow_replay_spool(caller_id="tester", request_token="tok")
    assert out["status"] == "ok"
    assert out["target"] == "main_memory"
    assert bound["name"] == "main_memory"


def test_shadow_hash_exists_in_main_reads_jsonl(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    mem = c.config["memory_dir"]
    mem.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": "a1", "normalized_text": "alpha"},
        {"id": "a2", "text": "beta"},
        "{bad json",
    ]
    p = mem / "auto_memory_records.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else x for x in rows), encoding="utf-8")
    from ms8.engine_core.security.shadow import content_hash

    assert c._shadow_hash_exists_in_main(content_hash("alpha")) is True
    assert c._shadow_hash_exists_in_main(content_hash("beta")) is True
    assert c._shadow_hash_exists_in_main(content_hash("gamma")) is False


def test_purge_test_memory_data_partial_and_pipeline_success(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c.working_memory = SimpleNamespace(purge_test_rows=lambda: (_ for _ in ()).throw(RuntimeError("wm")))
    c.auto_memory = SimpleNamespace(
        pipeline=SimpleNamespace(cleanup_test_pollution=lambda: {"status": "success", "removed": 3})
    )
    out = c.purge_test_memory_data()
    assert out["status"] == "partial"
    assert out["working_memory"]["status"] == "error"
    assert out["auto_memory_pipeline"]["status"] == "success"


def test_purge_test_memory_data_skips_when_no_pipeline_cleanup(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c.working_memory = SimpleNamespace(purge_test_rows=lambda: {"status": "success", "removed": 0})
    c.auto_memory = SimpleNamespace(pipeline=SimpleNamespace())  # no cleanup_test_pollution
    out = c.purge_test_memory_data()
    assert out["status"] == "success"
    assert out["auto_memory_pipeline"]["status"] == "skipped"


def test_backfill_auto_memory_record_ids_updates_from_meta(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    mem = c.config["memory_dir"]
    mem.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": "", "meta": {"id": "m1"}, "text": "a"},
        {"id": "x2", "text": "b"},
        "{oops",
    ]
    (mem / "auto_memory_records.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else x for x in rows) + "\n",
        encoding="utf-8",
    )
    orig_normalize = core_mod.normalize_memory_records
    core_mod.normalize_memory_records = lambda _memory_dir: {"status": "success"}  # type: ignore[assignment]
    try:
        out = c.backfill_auto_memory_record_ids()
    finally:
        core_mod.normalize_memory_records = orig_normalize
    assert out["status"] == "success"
    assert out["updated"] == 1
    after = (mem / "auto_memory_records.jsonl").read_text(encoding="utf-8")
    assert '"id": "m1"' in after


def test_backfill_auto_memory_record_ids_skips_when_file_missing(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    out = c.backfill_auto_memory_record_ids()
    assert out["status"] == "skipped"
    assert out["reason"] == "records_missing"


def test_semantic_and_feedback_error_wrappers(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c.semantic_search = SimpleNamespace(repair_missing_dense=lambda limit=80: (_ for _ in ()).throw(RuntimeError("ss")))
    c.knowledge_feedback = SimpleNamespace(
        rebuild_balanced_feedback=lambda window=None: (_ for _ in ()).throw(RuntimeError("kf"))
    )
    out_sem = c.repair_semantic_cache(limit=7)
    out_fb = c.rebalance_feedback_distribution(window=20)
    assert out_sem["status"] == "error"
    assert "ss" in out_sem["error"]
    assert out_fb["status"] == "error"
    assert "kf" in out_fb["error"]


def test_approve_threshold_suggestion_integrity_invalid(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    logs: list[dict[str, object]] = []
    c._load_threshold_pending = lambda: {"_integrity_valid": False, "items": []}  # type: ignore[method-assign]
    c._append_threshold_approval_log = lambda payload: logs.append(payload)  # type: ignore[method-assign]
    out = c.approve_threshold_suggestion("a1", approver="u", confirm=True)
    assert out["status"] == "error"
    assert out["error"] == "pending_suggestions_integrity_invalid"
    assert logs and logs[0]["event"] == "integrity_invalid"


def test_reject_threshold_suggestion_paths(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    saved: dict[str, object] = {}
    logs: list[dict[str, object]] = []
    payload = {"_integrity_valid": True, "items": [{"approval_id": "a1", "status": "pending"}]}
    c._load_threshold_pending = lambda: payload  # type: ignore[method-assign]
    c._save_threshold_pending = lambda p: saved.update(p)  # type: ignore[method-assign]
    c._append_threshold_approval_log = lambda item: logs.append(item)  # type: ignore[method-assign]
    out = c.reject_threshold_suggestion("a1", approver="reviewer", reason="x")
    assert out["status"] == "success"
    assert any(i.get("event") == "rejected" for i in logs)
    assert saved["items"][0]["status"] == "rejected"  # type: ignore[index]


def test_repair_graph_access_counts_disabled_branches(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c._graph_enabled = lambda: False  # type: ignore[method-assign]
    c.knowledge_graph = None
    out_disabled = c.repair_graph_access_counts()
    assert out_disabled["status"] == "disabled"

    c._graph_enabled = lambda: True  # type: ignore[method-assign]
    c.knowledge_graph = None
    out_no_kg = c.repair_graph_access_counts()
    assert out_no_kg["status"] == "disabled"
