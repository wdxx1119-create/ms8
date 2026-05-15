from __future__ import annotations
import re
from typing import Dict, List

ENV_SCOPES = ["开发环境", "测试环境", "生产环境", "dev", "test", "staging", "prod", "production"]


def _has_scope_split(payload: str) -> bool:
    lowered = payload.lower()
    matched = [scope for scope in ENV_SCOPES if scope in payload or scope in lowered]
    if len(set(matched)) >= 2:
        return True
    if re.search(r"配置\s*[a-zA-Z0-9]+", payload, flags=re.IGNORECASE):
        return True
    return False


def evaluate_conflict(text: str) -> Dict[str, object]:
    payload = str(text or "")
    lowered = payload.lower()
    conflict_flags: List[str] = []
    resolution = "coexist"
    entity = ""
    attribute = ""
    value = ""
    reason = ""

    scoped = _has_scope_split(payload)

    if (("启用" in payload and "禁用" in payload) or ("enabled" in lowered and "disabled" in lowered)) and not scoped:
        conflict_flags.append("state_conflict")
        resolution = "pending_review"
        attribute = "status"
        reason = "state_enabled_disabled_in_same_statement"
    if ("应该使用" in payload and "不要使用" in payload) or ("should use" in lowered and "do not use" in lowered):
        conflict_flags.append("negation_conflict")
        resolution = "pending_review"
        reason = reason or "opposite_intent_use_vs_not_use"
    num_hits = re.findall(r"(?:阈值|threshold|保留|retention)\s*(?:为|=|:)?\s*(\d+(?:\.\d+)?)", lowered)
    if len(set(num_hits)) >= 2 and not scoped:
        conflict_flags.append("numeric_conflict")
        resolution = "pending_review"
        attribute = attribute or "numeric_setting"
        reason = reason or "multiple_numeric_values_found"
    if ("原来" in payload and "现在" in payload and "改" in payload) or ("previously" in lowered and "now" in lowered):
        conflict_flags.append("temporal_evolution")
        resolution = "replace_old"
        reason = reason or "explicit_temporal_update_detected"
    if ("必须" in payload and "可选" in payload) or ("must" in lowered and "optional" in lowered):
        conflict_flags.append("mutually_exclusive_terms")
        resolution = "pending_review"
        reason = reason or "contains_mutually_exclusive_terms"

    if not conflict_flags and scoped and (("启用" in payload and "禁用" in payload) or len(set(num_hits)) >= 2):
        reason = "scoped_configuration_coexist"

    return {
        "has_conflict": bool(conflict_flags),
        "conflict_type": conflict_flags[0] if conflict_flags else "",
        "conflict_flags": conflict_flags,
        "resolution": resolution if conflict_flags else "coexist",
        "reason": reason,
        "entity": entity,
        "attribute": attribute,
        "value": value,
        "timestamp": "",
    }


def detect_conflict(text: str) -> tuple[bool, str]:
    result = evaluate_conflict(text)
    return bool(result["has_conflict"]), str(result["reason"])
