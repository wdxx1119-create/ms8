from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List


@dataclass
class ReviewItem:
    memory_id: str
    reason: str = ""
    confidence: float = 0.0
    category: str = ""
    decision: str = "pending"
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    risk_level: str = "medium"
    review_reason: str = ""

    def __post_init__(self) -> None:
        r = str(self.reason or "").strip()
        rr = str(self.review_reason or "").strip()
        if not r and rr:
            self.reason = rr
        elif r and not rr:
            self.review_reason = r
        elif not r and not rr:
            self.reason = "unspecified_review"
            self.review_reason = "unspecified_review"


@dataclass
class BatchReviewResult:
    reviewed: int
    accepted: int
    rejected: int
    items: List[ReviewItem] = field(default_factory=list)
