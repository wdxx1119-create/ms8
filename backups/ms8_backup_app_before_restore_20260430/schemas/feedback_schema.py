from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeedbackItem:
    memory_id: str
    signal: str
    category: str
    helpful: bool
