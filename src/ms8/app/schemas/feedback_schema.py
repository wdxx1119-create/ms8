from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class FeedbackItem:
    memory_id: str
    signal: str
    category: str
    helpful: bool
    note: str = ""
    source: str = ""
    confidence: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
