from __future__ import annotations

import re


def extract_action_object(text: str) -> tuple[str, str, str]:
    lowered = text.lower()
    if "完成" in text or "done" in lowered:
        return "complete", "task", "done"
    if "计划" in text or "plan" in lowered:
        return "plan", "next_step", "planned"
    m = re.search(r"(?:决定|采用|选择)\s*([^，。\n]+)", text)
    if m:
        return "decide", m.group(1).strip(), "decided"
    return "note", "context", "active"
