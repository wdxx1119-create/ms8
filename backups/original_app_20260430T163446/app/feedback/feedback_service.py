from __future__ import annotations

import json
from pathlib import Path
from typing import List

from app.schemas.feedback_schema import FeedbackItem


class FeedbackService:
    def __init__(self, store_path: Path | None = None) -> None:
        self.items: List[FeedbackItem] = []
        self.store_path = store_path
        if self.store_path is not None:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            if self.store_path.exists():
                self._load()

    def add(self, item: FeedbackItem) -> None:
        self.items.append(item)
        self._append(item)

    def by_category(self, category: str) -> List[FeedbackItem]:
        return [i for i in self.items if i.category == category]

    def recent(self, limit: int = 1000) -> List[FeedbackItem]:
        return self.items[-max(1, limit):]

    def _load(self) -> None:
        if self.store_path is None or not self.store_path.exists():
            return
        for line in self.store_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                self.items.append(FeedbackItem(**raw))
            except TypeError:
                continue

    def _append(self, item: FeedbackItem) -> None:
        if self.store_path is None:
            return
        with self.store_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item.__dict__, ensure_ascii=False) + "\n")
