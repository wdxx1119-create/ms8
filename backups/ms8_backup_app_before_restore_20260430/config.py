from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DedupeConfig:
    similarity_threshold: float = 0.9
    similar_soft_threshold: float = 0.6
    similar_hard_threshold: float = 0.95
    similar_window_minutes: int = 120
    category_repeat_thresholds: dict[str, int] | None = None


@dataclass
class QualityGateConfig:
    min_confidence: float = 0.5
    min_len_cjk: int = 4
    min_len_non_cjk: int = 8


@dataclass
class ThresholdConfig:
    accept_confidence: float = 0.62
    reject_confidence: float = 0.2
