from __future__ import annotations

import collections
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.core import MemoryCore


def _core(tmp_path: Path) -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c._recent_query_tokens = collections.deque(maxlen=24)
    c._utc_now = lambda: datetime(2026, 5, 25, tzinfo=timezone.utc)  # type: ignore[method-assign]
    c.config = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {"memory": {"working_memory": {}}},
    }
    c._run_async = lambda x: x  # type: ignore[method-assign]
    c._graph_enabled = lambda: False  # type: ignore[method-assign]
    c._dispatch_graph_batch_extract = lambda: None  # type: ignore[method-assign]
    return c


def test_learning_wrappers_and_restore_reindex(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.learning = None
    c.reindex_memory = lambda: None  # type: ignore[method-assign]
    c.run_learning_tasks()
    c.trigger_daily_learning()
    assert c.trigger_weekly_compression()["status"] == "disabled"
    assert c.list_archived_logs() == []
    assert c.restore_archived_logs()["status"] == "disabled"

    calls: list[str] = []
    c.learning = SimpleNamespace(
        run_pending_tasks=lambda: calls.append("run"),
        trigger_daily_learning=lambda date_str=None: calls.append(f"daily:{date_str}"),  # noqa: ANN001
        trigger_weekly_compression=lambda confirm=False: {"status": "success", "confirm": confirm},  # noqa: ANN001
        list_archived_logs=lambda limit=50: [{"limit": limit}],  # noqa: ANN001
        restore_archived_logs=lambda date_prefix=None, limit=None: ["a.md"],  # noqa: ANN001
    )
    c.reindex_memory = lambda: calls.append("reindex")  # type: ignore[method-assign]
    c._graph_enabled = lambda: True  # type: ignore[method-assign]
    c._dispatch_graph_batch_extract = lambda: calls.append("kg")  # type: ignore[method-assign]
    c.run_learning_tasks()
    c.trigger_daily_learning("2026-05-24")
    assert c.trigger_weekly_compression(confirm=True)["confirm"] is True
    assert c.list_archived_logs(limit=7)[0]["limit"] == 7
    restored = c.restore_archived_logs()
    assert restored["status"] == "success"
    assert restored["restored"] == ["a.md"]
    assert "reindex" in calls and "kg" in calls


def test_synthetic_wrappers_disabled_and_success(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.synthesizer = None
    assert c.generate_synthetic_candidates()["status"] == "disabled"
    assert c.list_synthetic_candidates()["status"] == "disabled"
    assert c.confirm_synthetic_candidates()["status"] == "disabled"
    assert c.reject_synthetic_candidates(["x"])["status"] == "disabled"
    assert c.review_synthetic_candidates([{"id": "x"}])["status"] == "disabled"
    assert c.get_synthetic_health()["status"] == "disabled"
    assert c.preview_rollback_auto_approved_synthetic()["status"] == "disabled"
    assert c.rollback_auto_approved_synthetic()["status"] == "disabled"
    assert c.rebalance_synthetic_candidates()["status"] == "disabled"
    assert c.discover_synthetic_gaps()["status"] == "disabled"

    c.synthesizer = SimpleNamespace(
        generate_candidates=lambda limit=20: [{"id": "c1", "limit": limit}],  # noqa: ANN001
        list_candidates=lambda status="review", limit=20: [{"status": status, "limit": limit}],  # noqa: ANN001
        confirm_candidates=lambda candidate_ids=None, min_score=None: {"accepted": candidate_ids or [], "min": min_score},  # noqa: ANN001,E501
        reject_candidates=lambda candidate_ids: {"rejected": candidate_ids},  # noqa: ANN001
        review_candidates=lambda decisions: {"accepted": 1, "rejected": 2, "decisions": decisions},  # noqa: ANN001
        health_report=lambda: {"ok": True},
        preview_rollback_auto_approved=lambda since_hours=1: {"status": "success", "since_hours": since_hours},  # noqa: ANN001,E501
        rollback_auto_approved=lambda since_hours=1: {"status": "success", "since_hours": since_hours},  # noqa: ANN001,E501
        rebalance_review_queue=lambda max_auto_accept=40, apply_writeback=False: {"accepted": 2, "rejected": 1, "apply_writeback": apply_writeback},  # noqa: ANN001,E501
        discover_gaps=lambda limit=10: {"status": "success", "gaps": [{"limit": limit}]},  # noqa: ANN001
    )
    assert c.generate_synthetic_candidates(limit=3)["candidates"][0]["limit"] == 3
    assert c.list_synthetic_candidates(status="pending", limit=5)["candidates"][0]["status"] == "pending"
    assert c.confirm_synthetic_candidates(candidate_ids=["a"], min_score=0.9)["accepted"] == ["a"]
    assert c.reject_synthetic_candidates(["x"])["rejected"] == ["x"]
    assert c.review_synthetic_candidates([{"id": "a"}])["status"] == "success"
    assert c.get_synthetic_health()["health"]["ok"] is True
    assert c.preview_rollback_auto_approved_synthetic(since_hours=2)["since_hours"] == 2
    assert c.rollback_auto_approved_synthetic(since_hours=4)["since_hours"] == 4
    assert c.rebalance_synthetic_candidates(apply_writeback=True)["status"] == "success"
    assert c.discover_synthetic_gaps(limit=2)["gaps"][0]["limit"] == 2


def test_feedback_review_and_meta_wrappers(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.auto_memory = None
    assert c.record_memory_feedback("m", "c", "s", True)["status"] == "disabled"
    assert c.weekly_threshold_suggestions()["status"] == "disabled"
    assert c.list_pending_reviews()["status"] == "disabled"
    assert c.batch_review()["status"] == "disabled"
    assert c.relabel_review_item("m", "x")["status"] == "disabled"

    c.auto_memory = SimpleNamespace(
        pipeline=SimpleNamespace(
            review_pending=lambda: [{"id": "p1"}],
            review_service=object(),
        ),
        record_feedback=lambda **kw: {"status": "success", **kw},  # noqa: ANN001
        weekly_threshold_suggestions=lambda: {"status": "success"},
    )
    out_fb = c.record_memory_feedback("m1", "pref", "positive", True, note="n", source="user", confidence=0.7)
    assert out_fb["status"] == "success"
    assert c.weekly_threshold_suggestions()["status"] == "success"
    assert c.list_pending_reviews()["items"][0]["id"] == "p1"

    err_batch = c.batch_review(mode="accept_all")
    assert err_batch["status"] in {"error", "disabled"}
    err_relabel = c.relabel_review_item("m1", "x")
    assert err_relabel["status"] in {"error", "disabled"}

    c.meta_cognition = None
    assert c.run_meta_cognition()["status"] == "disabled"
    assert c.get_meta_cognition_status()["status"] == "disabled"
    c.meta_cognition = SimpleNamespace(
        self_monitor=lambda conversations, period="daily": SimpleNamespace(to_dict=lambda: {"period": period, "count": len(conversations)}),  # noqa: ANN001,E501
        get_status=lambda: {"ready": True},
    )
    c.short_term_memory = ["u1", "u2", "u3"]
    run = c.run_meta_cognition(period="weekly")
    assert run["status"] == "success"
    assert run["report"]["period"] == "weekly"
    assert c.get_meta_cognition_status()["ready"] is True


def test_context_optimization_suggestions_branches(tmp_path: Path) -> None:
    c = _core(tmp_path)
    wm = tmp_path / "memory"
    wm.mkdir(parents=True, exist_ok=True)
    c.config["settings"]["memory"]["working_memory"]["context_snapshot_log_file"] = str(wm / "snap.jsonl")

    out_missing = c.get_context_optimization_suggestions()
    assert out_missing["status"] == "skipped"

    snap = wm / "snap.jsonl"
    snap.write_text("{bad}\n", encoding="utf-8")
    out_empty = c.get_context_optimization_suggestions(window=30)
    assert out_empty["status"] == "skipped"

    rows = []
    for i in range(30):
        rows.append(
            {
                "complexity_level": "simple" if i < 15 else "complex",
                "budget_chars": 1000,
                "used_chars": 200 if i < 15 else 900,
                "fallback_used": i % 3 == 0,
                "blocked_count": 3,
                "high_trust_ratio": 0.5 if i >= 15 else 0.8,
                "hypothesis_ratio": 0.4,
                "recent_topic_consistency": 0.1,
            }
        )
    snap.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    report = c.get_context_optimization_suggestions(window=30)
    assert report["status"] == "success"
    assert report["suggestions"]
    assert Path(report["report_file"]).exists()
