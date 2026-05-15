from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MemoryIndexer:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._container_mode = "list"
        self._docs: list[dict[str, Any]] = []

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def add(self, doc: dict[str, Any]) -> None:
        item = dict(doc)
        item.setdefault("created_at", self._now_iso())
        self._docs.append(item)
        self._persist()

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._container_mode == "dict":
            hot_items = [d for d in self._docs if float(d.get("confidence", 0.0) or 0.0) >= 0.6]
            cold_items = [d for d in self._docs if float(d.get("confidence", 0.0) or 0.0) < 0.6]
            payload = {"hot_items": hot_items, "cold_items": cold_items}
        else:
            payload = self._docs
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = str(query or "").strip().lower()
        rows = [d for d in self._docs if q in str(d.get("normalized_text", d)).lower()]
        rows.sort(key=lambda d: float(d.get("confidence", 0.0) or 0.0), reverse=True)
        return rows[: max(1, int(limit))]
