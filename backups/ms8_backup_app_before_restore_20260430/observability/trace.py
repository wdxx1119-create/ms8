from __future__ import annotations

import uuid
from typing import Any, Dict, List


class TraceCollector:
    def __init__(self) -> None:
        self.trace_id = str(uuid.uuid4())
        self.events: List[Dict[str, Any]] = []

    def add(self, stage: str, detail: Dict[str, Any]) -> None:
        self.events.append({"stage": stage, "detail": detail})

    def dump(self) -> Dict[str, Any]:
        return {"trace_id": self.trace_id, "events": self.events}
