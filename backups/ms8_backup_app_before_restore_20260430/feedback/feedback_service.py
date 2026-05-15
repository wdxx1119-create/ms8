from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any


class FeedbackService:
    def __init__(self, store_path: Path) -> None:
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.store_path.exists():
            self.store_path.write_text("", encoding="utf-8")

    def add(self, item: Any) -> None:
        row = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
        with self.store_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def list_rows(self) -> list[dict[str, Any]]:
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
