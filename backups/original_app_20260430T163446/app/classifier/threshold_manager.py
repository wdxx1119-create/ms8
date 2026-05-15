from __future__ import annotations

from app.config import ThresholdConfig


class ThresholdManager:
    def __init__(self, cfg: ThresholdConfig) -> None:
        self.cfg = cfg

    def required(self, category: str) -> float:
        return float(self.cfg.category_thresholds.get(category, self.cfg.global_min_confidence))

    def pass_threshold(self, category: str, confidence: float) -> bool:
        return confidence >= self.required(category)

    def set_category_threshold(self, category: str, value: float) -> float:
        bounded = min(0.95, max(0.30, float(value)))
        self.cfg.category_thresholds[category] = round(bounded, 3)
        return self.cfg.category_thresholds[category]

    def snapshot(self) -> dict:
        return {
            "global_min_confidence": float(self.cfg.global_min_confidence),
            "category_thresholds": dict(self.cfg.category_thresholds),
        }
