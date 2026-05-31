from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.app.classifier.threshold_manager import ThresholdManager
from ms8.app.config import ThresholdConfig
from ms8.app.feedback.feedback_service import FeedbackService
from ms8.app.feedback.rule_optimizer import RuleOptimizer
from ms8.app.memory.indexer import MemoryIndexer
from ms8.app.review.batch_review import BatchReview
from ms8.app.review.review_service import ReviewService
from ms8.app.schemas.feedback_schema import FeedbackItem
from ms8.app.schemas.review_schema import ReviewItem


def _feedback(memory_id: str, category: str, helpful: bool, created_at: str) -> FeedbackItem:
    return FeedbackItem(
        memory_id=memory_id,
        signal="useful",
        category=category,
        helpful=helpful,
        note="n",
        source="s",
        confidence=0.7,
        created_at=created_at,
    )


def test_feedback_service_load_filters_invalid_rows(tmp_path: Path) -> None:
    store = tmp_path / "feedback.jsonl"
    good = _feedback("m1", "decision", True, datetime.now(timezone.utc).isoformat())
    bad_json = "{bad"
    bad_shape = json.dumps({"memory_id": "x"})
    store.write_text("\n".join([json.dumps(good.__dict__), bad_json, bad_shape]), encoding="utf-8")

    svc = FeedbackService(store)
    assert len(svc.items) == 1
    assert svc.by_category("decision")[0].memory_id == "m1"
    assert svc.recent(limit=1)[0].memory_id == "m1"


def test_rule_optimizer_adjust_and_suggest(tmp_path: Path) -> None:
    store = tmp_path / "feedback.jsonl"
    svc = FeedbackService(store)
    cfg = ThresholdConfig()
    tm = ThresholdManager(cfg)
    opt = RuleOptimizer(
        feedback_service=svc,
        threshold_manager=tm,
        low_ratio=0.4,
        high_ratio=0.75,
        raise_step=0.05,
        drop_step=0.05,
        floor_threshold=0.45,
    )

    base = tm.required("decision")
    # no feedback -> unchanged
    assert opt.optimize_category("decision") == base

    now = datetime.now(timezone.utc)
    # low helpful ratio -> raise threshold
    for i in range(6):
        svc.add(_feedback(f"low-{i}", "decision", helpful=(i == 0), created_at=(now - timedelta(hours=1)).isoformat()))
    raised = opt.optimize_category("decision")
    assert raised > base

    # high helpful ratio for another category -> lower threshold
    base_pref = tm.required("preference")
    for i in range(6):
        svc.add(_feedback(f"high-{i}", "preference", helpful=True, created_at=(now - timedelta(hours=1)).isoformat()))
    lowered = opt.optimize_category("preference")
    assert lowered <= base_pref

    out_path = tmp_path / "suggestions.json"
    payload = opt.suggest_threshold_updates(lookback_days=7, min_samples=5, output_path=out_path)
    assert out_path.exists()
    assert "suggestions" in payload
    assert isinstance(payload["suggestions"], list)


def test_batch_review_modes_and_relabel(tmp_path: Path) -> None:
    queue = tmp_path / "review.jsonl"
    service = ReviewService(queue)
    service.enqueue(ReviewItem(memory_id="a", confidence=0.9, risk_level="low", category="decision"))
    service.enqueue(ReviewItem(memory_id="b", confidence=0.1, risk_level="high", category="decision"))
    service.enqueue(ReviewItem(memory_id="c", confidence=0.6, risk_level="medium", category="plan"))
    batch = BatchReview(service)

    r1 = batch.apply(mode="triage_default", limit=10, accept_conf_min=0.8, reject_conf_max=0.2)
    assert r1.reviewed == 2
    assert r1.accepted == 1
    assert r1.rejected == 1

    # add pending backlog for drain
    service.enqueue(ReviewItem(memory_id="d", confidence=0.4, risk_level="critical", category="plan"))
    service.enqueue(ReviewItem(memory_id="e", confidence=0.8, risk_level="low", category="plan"))
    r2 = batch.apply(mode="drain_backlog", drain_reject_conf_max=0.5, per_category_limit=1)
    assert r2.rejected >= 1

    assert batch.relabel("e", "configuration", notes="ok") is True
    assert batch.relabel("missing", "configuration") is False


def test_memory_indexer_load_dict_payload_search_and_cleanup(tmp_path: Path) -> None:
    idx_path = tmp_path / "idx.json"
    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    payload = {
        "items": [
            {"id": "1", "normalized_text": "project decision alpha", "confidence": 0.9, "created_at": now},
            {"id": "2", "normalized_text": "legacy rejected item", "confidence": 0.8, "created_at": old, "status": "rejected"},
            {"id": "3", "normalized_text": "verify_canary 样本", "confidence": 0.9, "created_at": now, "source": "verify_canary_x"},
        ],
        "hot_items": [],
        "cold_items": [],
    }
    idx_path.write_text(json.dumps(payload), encoding="utf-8")

    idx = MemoryIndexer(idx_path, hot_min_confidence=0.65, excluded_source_prefixes=["verify_canary"])
    # add via journal-replay path
    idx.journal_path.write_text(
        json.dumps({"id": "4", "normalized_text": "alpha beta", "confidence": 0.9, "created_at": now}) + "\n",
        encoding="utf-8",
    )
    idx._replay_journal()

    hits = idx.search("alpha", limit=5)
    assert any(h["id"] == "1" for h in hits)
    assert any(h["id"] == "4" for h in hits)

    removed_excluded = idx.cleanup_excluded()
    assert removed_excluded["removed"] >= 1
    removed_rejected = idx.cleanup_rejected()
    assert removed_rejected["removed"] >= 1

