from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from app.schemas.review_schema import ReviewItem


class ReviewService:
    def __init__(self, store_path: Path | None = None) -> None:
        self._queue: List[ReviewItem] = []
        self.store_path = store_path
        if self.store_path is not None:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            if self.store_path.exists():
                self._load()

    def enqueue(self, item: ReviewItem) -> None:
        self._queue.append(item)
        self._persist_all()

    def list_pending(self) -> List[ReviewItem]:
        return [i for i in self._queue if i.decision == "pending"]

    def update(self, memory_id: str, decision: str, notes: str = "", category: str | None = None) -> bool:
        # Prefer updating a pending item first to avoid duplicate-id starvation.
        candidates = [i for i in self._queue if i.memory_id == memory_id]
        if not candidates:
            return False
        target = None
        for item in candidates:
            if str(item.decision) == "pending":
                target = item
                break
        if target is None:
            target = candidates[0]
        target.decision = decision
        target.notes = notes
        if category:
            target.category = category
        target.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist_all()
        return True

    def list_high_risk(self) -> List[ReviewItem]:
        return [i for i in self.list_pending() if i.risk_level in {"high", "critical"}]

    def _load(self) -> None:
        if self.store_path is None or not self.store_path.exists():
            return
        for line in self.store_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                self._queue.append(ReviewItem(**raw))
            except Exception:
                continue

    def _persist_all(self) -> None:
        if self.store_path is None:
            return
        lines = [json.dumps(item.__dict__, ensure_ascii=False) for item in self._queue]
        self.store_path.write_text(("\n".join(lines) + ("\n" if lines else "")), encoding="utf-8")
