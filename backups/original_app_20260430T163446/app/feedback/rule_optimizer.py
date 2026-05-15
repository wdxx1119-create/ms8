from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from app.classifier.threshold_manager import ThresholdManager
from app.feedback.feedback_service import FeedbackService
from memory.file_write_guard import atomic_write_json


class RuleOptimizer:
    """Lightweight user-feedback-driven threshold tuning."""

    def __init__(
        self,
        feedback_service: FeedbackService,
        threshold_manager: ThresholdManager,
        low_ratio: float = 0.4,
        high_ratio: float = 0.75,
        raise_step: float = 0.03,
        drop_step: float = 0.02,
        floor_threshold: float = 0.45,
    ) -> None:
        self.feedback_service = feedback_service
        self.threshold_manager = threshold_manager
        self.low_ratio = float(low_ratio)
        self.high_ratio = float(high_ratio)
        self.raise_step = float(raise_step)
        self.drop_step = float(drop_step)
        self.floor_threshold = float(floor_threshold)

    def optimize_category(self, category: str) -> float:
        feedbacks = self.feedback_service.by_category(category)
        if not feedbacks:
            return self.threshold_manager.required(category)
        helpful = sum(1 for f in feedbacks if f.helpful)
        ratio = helpful / max(1, len(feedbacks))
        current = self.threshold_manager.cfg.category_thresholds.get(category, self.threshold_manager.cfg.global_min_confidence)
        if ratio < self.low_ratio:
            current = min(0.9, current + self.raise_step)
        elif ratio > self.high_ratio:
            current = max(self.floor_threshold, current - self.drop_step)
        self.threshold_manager.cfg.category_thresholds[category] = round(current, 3)
        return current

    def suggest_threshold_updates(
        self,
        lookback_days: int = 7,
        min_samples: int = 5,
        output_path: Path | None = None,
    ) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))
        grouped = defaultdict(list)
        for item in self.feedback_service.recent(limit=5000):
            try:
                ts_text = str(item.created_at)
                if ts_text.endswith("Z"):
                    ts_text = ts_text[:-1] + "+00:00"
                ts = datetime.fromisoformat(ts_text)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                else:
                    ts = ts.astimezone(timezone.utc)
            except Exception:
                ts = datetime.now(timezone.utc)
            if ts < cutoff:
                continue
            grouped[item.category].append(item)

        suggestions = []
        for category, rows in grouped.items():
            if len(rows) < min_samples:
                continue
            helpful = sum(1 for r in rows if r.helpful)
            ratio = helpful / max(1, len(rows))
            current = self.threshold_manager.required(category)
            suggested = current
            reason = "keep"
            if ratio < self.low_ratio:
                suggested = min(0.9, current + self.raise_step)
                reason = "quality_low_raise_threshold"
            elif ratio > self.high_ratio:
                suggested = max(self.floor_threshold, current - self.drop_step)
                reason = "quality_high_lower_threshold"
            suggestions.append(
                {
                    "category": category,
                    "samples": len(rows),
                    "helpful_ratio": round(ratio, 4),
                    "current_threshold": round(current, 3),
                    "suggested_threshold": round(suggested, 3),
                    "reason": reason,
                    "auto_applied": False,
                }
            )

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": lookback_days,
            "min_samples": min_samples,
            "suggestions": sorted(suggestions, key=lambda x: x["samples"], reverse=True),
        }
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(output_path, payload, ensure_ascii=False, indent=2)
        return payload
