from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any


class ReviewService:
    def __init__(self, store_path: Path) -> None:
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.store_path.exists():
            self.store_path.write_text("", encoding="utf-8")

    def _load(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for ln in self.store_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows

    def _save(self, rows: list[dict[str, Any]]) -> None:
        payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else "")
        self.store_path.write_text(payload, encoding="utf-8")

    def enqueue(self, item: Any) -> None:
        rows = self._load()
        row = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
        row.setdefault("decision", "pending")
        rows.append(row)
        self._save(rows)

    def list_pending(self) -> list[dict[str, Any]]:
        return [r for r in self._load() if str(r.get("decision", "pending")) == "pending"]

    def apply_decision_all_pending(self, decision: str) -> dict[str, int]:
        rows = self._load()
        reviewed = 0
        for row in rows:
            if str(row.get("decision", "pending")) == "pending":
                row["decision"] = decision
                reviewed += 1
        self._save(rows)
        return {"reviewed": reviewed}
