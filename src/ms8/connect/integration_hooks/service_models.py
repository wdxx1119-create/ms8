from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryCandidate:
    content: str
    source: str = "mcp:unknown"
    category: str = "general"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> MemoryCandidate:
        data = payload if isinstance(payload, dict) else {}
        content = str(data.get("content") or data.get("text") or "").strip()
        source = str(data.get("source") or "mcp:submit").strip()
        category = str(data.get("category") or "general").strip()
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        return cls(content=content, source=source, category=category, metadata=metadata)
