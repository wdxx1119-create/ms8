from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReviewItem:
    memory_id: str
    reason: str
    confidence: float
    category: str
    risk_level: str = "normal"
    decision: str = "pending"
