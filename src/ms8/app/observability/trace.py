from __future__ import annotations

import uuid
from typing import Any


class TraceCollector:
    def __init__(self) -> None:
        self.trace_id = str(uuid.uuid4())
        self.events: list[dict[str, Any]] = []

    def add(self, stage: str, detail: dict[str, Any]) -> None:
        self.events.append({"stage": stage, "detail": detail})

    def dump(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "events": self.events}
