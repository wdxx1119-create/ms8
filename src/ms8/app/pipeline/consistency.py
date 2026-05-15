from __future__ import annotations

from ms8.app.rules.conflict_rules import detect_conflict


def consistency_check(text: str) -> tuple[bool, bool, str]:
    conflict, reason = detect_conflict(text)
    return (not conflict, conflict, reason)
