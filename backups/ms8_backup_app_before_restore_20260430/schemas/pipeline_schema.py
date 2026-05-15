from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryRecord:
    text: str
    normalized_text: str = ""
    source: str = "unknown"
    category: str = "general"
    status: str = "accepted"
    confidence: float = 1.0
    meta: dict[str, Any] = field(default_factory=dict)
