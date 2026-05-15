from __future__ import annotations

from pathlib import Path

from ms8.app.review.review_service import ReviewService


def test_review_service_tracks_invalid_rows(tmp_path: Path) -> None:
    queue_file = tmp_path / "memory" / "auto_memory_review_queue.jsonl"
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text('{"memory_id":"m1","decision":"pending"}\n{bad json}\n', encoding="utf-8")

    svc = ReviewService(queue_file)
    assert len(svc.list_pending()) == 1
    assert svc.load_error_count == 1


def test_review_service_persist_success_clears_last_error(tmp_path: Path) -> None:
    queue_file = tmp_path / "memory" / "auto_memory_review_queue.jsonl"
    svc = ReviewService(queue_file)
    assert svc.last_persist_error == ""
    # enqueue triggers persist path
    from ms8.app.schemas.review_schema import ReviewItem

    svc.enqueue(ReviewItem(memory_id="m1"))
    assert svc.last_persist_error == ""

