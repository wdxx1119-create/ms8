from __future__ import annotations

import re
from typing import Dict, List


class ContextBuilder:
    def build(self, text: str, latest_memories: List[dict]) -> Dict:
        links = re.findall(r"https?://\S+", text)
        files = re.findall(r"\b[\w./-]+\.(?:py|js|ts|md|yaml|yml|json|toml|sql|sh)\b", text)
        ctx = {
            "has_code": "```" in text or "[CODE_BLOCK]" in text,
            "links": links[:5],
            "files": files[:5],
            "recent_categories": [m.get("category") for m in latest_memories[:5]],
            "recent_tags": sorted({tag for m in latest_memories[:5] for tag in m.get("tags", [])})[:8],
        }
        return ctx
