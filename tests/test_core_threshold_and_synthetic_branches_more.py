from __future__ import annotations

import collections
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ms8.engine_core.core import MemoryCore


def _core_stub(tmp_path: Path) -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c._recent_query_tokens = collections.deque(maxlen=24)
    c.config = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {"memory": {}},
    }
    c._utc_now = lambda: datetime(2026, 5, 25, tzinfo=timezone.utc)  # type: ignore[method-assign]
    return c


def test_record_memory_feedback_and_synthetic_wrappers(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c.auto_memory = None
    assert c.record_memory_feedback("m1", "preference", "s", True)["status"] == "disabled"

    class _Auto:
        pipeline = object()

        @staticmethod
        def record_feedback(**kwargs):  # noqa: ANN003
            return {"status": "success", "echo": kwargs}

    c.auto_memory = _Auto()
    out = c.record_memory_feedback("m1", "preference", "sig", True, note="n", source="user", confidence=0.7)
    assert out["status"] == "success"
    assert out["echo"]["memory_id"] == "m1"

    c.synthesizer = None
    assert c.confirm_synthetic_candidates()["status"] == "disabled"
    assert c.reject_synthetic_candidates(["a"])["status"] == "disabled"

    class _Synth:
        @staticmethod
        def confirm_candidates(candidate_ids=None, min_score=None):  # noqa: ANN001
            return {"status": "success", "accepted": candidate_ids or [], "min_score": min_score}

        @staticmethod
        def reject_candidates(candidate_ids):  # noqa: ANN001
            return {"status": "success", "rejected": candidate_ids}

        @staticmethod
        def review_candidates(decisions):  # noqa: ANN001
            return {"accepted": 1, "rejected": 1, "decisions": decisions}

    c.synthesizer = _Synth()
    out_confirm = c.confirm_synthetic_candidates(candidate_ids=["c1"], min_score=0.9)
    assert out_confirm["status"] == "success"
    assert out_confirm["accepted"] == ["c1"]
    out_reject = c.reject_synthetic_candidates(["c2"])
    assert out_reject["rejected"] == ["c2"]
    out_review = c.review_synthetic_candidates([{"id": "c3", "decision": "accept"}])
    assert out_review["status"] == "success"
    assert out_review["accepted"] == 1


def test_approve_threshold_suggestion_branches(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    logs: list[dict] = []
    c._append_threshold_approval_log = lambda payload: logs.append(payload)  # type: ignore[method-assign]
    c._save_threshold_pending = lambda payload: None  # type: ignore[method-assign]

    c._load_threshold_pending = lambda: {"_integrity_valid": False, "items": []}  # type: ignore[method-assign]
    out_integrity = c.approve_threshold_suggestion("a1", approver="u", confirm=True)
    assert out_integrity["status"] == "error"
    assert out_integrity["error"] == "pending_suggestions_integrity_invalid"

    c._load_threshold_pending = lambda: {"_integrity_valid": True, "items": []}  # type: ignore[method-assign]
    out_not_found = c.approve_threshold_suggestion("a1", approver="u", confirm=True)
    assert out_not_found["error"] == "approval_id_not_found"

    item_done = {"approval_id": "a1", "status": "approved"}
    c._load_threshold_pending = lambda: {"_integrity_valid": True, "items": [item_done]}  # type: ignore[method-assign]
    out_done = c.approve_threshold_suggestion("a1", approver="u", confirm=True)
    assert out_done["status"] == "skipped"
    assert out_done["reason"] == "already_processed"

    item_pending = {"approval_id": "a2", "status": "pending", "stats": {}, "suggestions": [{"key": "k"}]}
    c._load_threshold_pending = lambda: {"_integrity_valid": True, "items": [item_pending]}  # type: ignore[method-assign]
    out_needs = c.approve_threshold_suggestion("a2", approver="u", confirm=False)
    assert out_needs["status"] == "requires_confirmation"

    c._apply_threshold_suggestions_to_workspace_config = lambda suggestions: {"status": "error", "why": "bad"}  # type: ignore[method-assign]
    out_apply_err = c.approve_threshold_suggestion("a2", approver="u", confirm=True)
    assert out_apply_err["status"] == "error"
    assert out_apply_err["approval_id"] == "a2"

    c._apply_threshold_suggestions_to_workspace_config = lambda suggestions: {"status": "success", "applied": suggestions}  # type: ignore[method-assign]
    out_ok = c.approve_threshold_suggestion("a2", approver="u", confirm=True)
    assert out_ok["status"] == "success"
    assert out_ok["approval_id"] == "a2"
    assert any(x.get("event") == "approved" for x in logs)


def test_reject_threshold_suggestion_branches(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    logs: list[dict] = []
    c._append_threshold_approval_log = lambda payload: logs.append(payload)  # type: ignore[method-assign]
    c._save_threshold_pending = lambda payload: None  # type: ignore[method-assign]

    c._load_threshold_pending = lambda: {"_integrity_valid": False, "items": []}  # type: ignore[method-assign]
    out_integrity = c.reject_threshold_suggestion("r1", approver="u")
    assert out_integrity["status"] == "error"

    c._load_threshold_pending = lambda: {"_integrity_valid": True, "items": []}  # type: ignore[method-assign]
    out_not_found = c.reject_threshold_suggestion("r1", approver="u")
    assert out_not_found["error"] == "approval_id_not_found"

    item_done = {"approval_id": "r2", "status": "approved"}
    c._load_threshold_pending = lambda: {"_integrity_valid": True, "items": [item_done]}  # type: ignore[method-assign]
    out_done = c.reject_threshold_suggestion("r2", approver="u")
    assert out_done["status"] == "skipped"

    item_pending = {"approval_id": "r3", "status": "pending"}
    payload = {"_integrity_valid": True, "items": [item_pending]}
    c._load_threshold_pending = lambda: payload  # type: ignore[method-assign]
    out_ok = c.reject_threshold_suggestion("r3", approver="u", reason="manual_reject")
    assert out_ok["status"] == "success"
    assert out_ok["decision"] == "rejected"
    assert item_pending["status"] == "rejected"
    assert any(x.get("event") == "rejected" for x in logs)


def test_threshold_helpers_additional_branches(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)

    # approve: empty id
    out_empty = c.approve_threshold_suggestion("", approver="u", confirm=True)
    assert out_empty["status"] == "error"
    assert out_empty["error"] == "approval_id_required"

    # list pending: payload non-dict currently raises (document current behavior)
    c._load_threshold_pending = lambda: []  # type: ignore[method-assign]
    with pytest.raises(AttributeError):
        c.list_pending_threshold_suggestions(include_processed=False)

    payload = {
        "_integrity_valid": True,
        "last_generated_at": "t1",
        "last_applied_at": "t2",
        "items": [
            {"approval_id": "a", "status": "pending"},
            {"approval_id": "b", "status": "approved"},
        ],
    }
    c._load_threshold_pending = lambda: payload  # type: ignore[method-assign]
    out_filtered = c.list_pending_threshold_suggestions(include_processed=False)
    assert len(out_filtered["items"]) == 1
    out_all = c.list_pending_threshold_suggestions(include_processed=True)
    assert len(out_all["items"]) == 2


def test_generate_threshold_suggestions_branches(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c.config["settings"]["memory"] = {"maintenance_policy": {"feedback_rebalance_window": 7}}
    c.knowledge_feedback = SimpleNamespace(
        build_weekly_threshold_suggestion=lambda window=0: {"status": "success", "suggestions": []}
    )
    c._queue_threshold_suggestions = lambda report, source="manual": {"approval_id": "x", "suggestions": []}  # type: ignore[method-assign]
    c._append_threshold_approval_log = lambda payload: None  # type: ignore[method-assign]

    # success but empty suggestions
    out_empty = c.generate_threshold_suggestions()
    assert out_empty["status"] == "success_no_suggestions"
    assert out_empty["queued"] is False

    # report status != success
    c.knowledge_feedback = SimpleNamespace(
        build_weekly_threshold_suggestion=lambda window=0: {"status": "error", "suggestions": []}
    )
    out_err_report = c.generate_threshold_suggestions()
    assert out_err_report["queued"] is False

    # enqueue disabled
    c.knowledge_feedback = SimpleNamespace(
        build_weekly_threshold_suggestion=lambda window=0: {"status": "success", "suggestions": [{"k": 1}]}
    )
    out_no_enqueue = c.generate_threshold_suggestions(enqueue_for_approval=False)
    assert out_no_enqueue["queued"] is False

def test_graph_and_learning_simple_wrappers(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c.learning = None
    assert c.trigger_daily_learning("2026-05-25") is None

    class _Learn:
        @staticmethod
        def trigger_daily_learning(date_str=None):  # noqa: ANN001
            return {"status": "ok", "date": date_str}

    c.learning = _Learn()
    c.knowledge_graph = None
    c.trigger_daily_learning("2026-05-25")

    c._graph_enabled = lambda: False  # type: ignore[method-assign]
    c.knowledge_graph = None
    assert c.batch_extract_knowledge_graph()["status"] == "disabled"
    assert c.run_knowledge_graph_maintenance()["status"] == "disabled"

    c._graph_enabled = lambda: True  # type: ignore[method-assign]
    c.knowledge_graph = SimpleNamespace(
        batch_extract_pending_memories=lambda limit=None, force=False: {
            "status": "success",
            "processed": 1,
            "force": force,
            "limit": limit,
        },
        decay_relation_weights=lambda: {"status": "success", "decayed": 1},
        cleanup_isolated_entities=lambda: {"status": "success", "removed": 1},
        health_check=lambda: {"status": "ok"},
        backfill_entity_access_from_anchors=lambda min_access=1: {"status": "success", "min_access": min_access},
    )
    c.retrieve_memories = lambda query, top_k=50: [{"text": "a"}]  # type: ignore[method-assign]
    out_extract = c.batch_extract_knowledge_graph(limit=1, force=True)
    assert out_extract["status"] == "success"
    assert out_extract["force"] is True
    out_maint = c.run_knowledge_graph_maintenance()
    assert out_maint["status"] == "success"
    out_repair = c.repair_graph_access_counts(min_access=2)
    assert out_repair["status"] == "success"
