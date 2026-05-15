from __future__ import annotations


def detect_conflict(text: str) -> tuple[bool, str]:
    # lightweight consistency/conflict hint: opposite connectors in one statement
    lowered = text.lower()
    if ("必须" in text and "可选" in text) or ("must" in lowered and "optional" in lowered):
        return True, "contains_mutually_exclusive_terms"
    return False, ""
