from __future__ import annotations

from typing import Dict, List


def compute_risk_scores(
    text: str,
    block_result: Dict[str, object],
    privacy_result: Dict[str, object],
    conflict_result: Dict[str, object],
) -> Dict[str, float]:
    payload = str(text or "")
    length = len(payload.strip())
    noise_score = 0.1
    if bool(block_result.get("blocked", False)):
        noise_score = 0.95
    elif length <= 6:
        noise_score = 0.55

    flags: List[str] = list(privacy_result.get("flags", [])) if isinstance(privacy_result.get("flags", []), list) else []
    privacy_risk_score = 0.0
    if flags:
        privacy_risk_score = 0.75
    if any(x in flags for x in {"ssh_private_key", "password_field"}):
        privacy_risk_score = 0.98

    conflict_risk_score = 0.0
    if bool(conflict_result.get("has_conflict", False)):
        conflict_risk_score = 0.6
    if str(conflict_result.get("resolution", "")) == "pending_review":
        conflict_risk_score = 0.82

    memory_value_score = 0.2
    if length > 10:
        memory_value_score = 0.5
    if any(k in payload for k in ["配置", "决定", "计划", "阈值", "enabled", "disabled", "version"]):
        memory_value_score = max(memory_value_score, 0.72)
    if bool(block_result.get("blocked", False)):
        memory_value_score = 0.05

    return {
        "noise_score": round(noise_score, 4),
        "privacy_risk_score": round(privacy_risk_score, 4),
        "conflict_risk_score": round(conflict_risk_score, 4),
        "memory_value_score": round(memory_value_score, 4),
    }
