from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RuleOptimizer:
    def __init__(self, feedback_service: Any, threshold_manager: Any) -> None:
        self.feedback_service = feedback_service
        self.threshold_manager = threshold_manager

    def suggest_threshold_updates(
        self,
        lookback_days: int = 7,
        min_samples: int = 5,
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        rows = self.feedback_service.list_rows() if hasattr(self.feedback_service, "list_rows") else []
        by_category: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            cat = str(row.get("category", "general"))
            by_category.setdefault(cat, []).append(row)

        suggestions: list[dict[str, Any]] = []
        for cat, items in by_category.items():
            if len(items) < int(min_samples):
                continue
            helpful_ratio = sum(1 for i in items if bool(i.get("helpful", False))) / max(1, len(items))
            suggestions.append(
                {
                    "category": cat,
                    "samples": len(items),
                    "helpful_ratio": round(helpful_ratio, 3),
                    "accept_confidence": round(float(getattr(self.threshold_manager, "accept_confidence", 0.62)), 3),
                }
            )

        payload = {
            "lookback_days": int(lookback_days),
            "min_samples": int(min_samples),
            "suggestions": suggestions,
        }
        if output_path is not None:
            Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload
