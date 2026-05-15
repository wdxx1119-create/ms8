from __future__ import annotations

from app.classifier.threshold_manager import ThresholdManager


def final_decision(category: str, confidence: float, threshold_manager: ThresholdManager, review_conf_threshold: float, conflict_flag: bool) -> tuple[str, bool, str]:
    if not threshold_manager.pass_threshold(category, confidence):
        return "rejected", True, "below_category_threshold"
    if conflict_flag:
        return "pending_review", True, "consistency_conflict"
    if confidence < review_conf_threshold:
        return "pending_review", True, "low_confidence"
    return "accepted", False, "accepted"
