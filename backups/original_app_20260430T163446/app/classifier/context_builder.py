from __future__ import annotations

from typing import Dict, List

try:
    from memory.context_material import assemble_shared_context_material, project_classification_context
except Exception:
    assemble_shared_context_material = None
    project_classification_context = None


class ContextBuilder:
    """Build lightweight classification context.

    Scope boundary:
    - This module only projects classification view.
    - Shared raw material is assembled once in memory.context_material.
    - Response-time injection stays in memory.working_memory / MemoryCore.
    """

    def build(self, text: str, latest_memories: List[dict]) -> Dict:
        if assemble_shared_context_material and project_classification_context:
            material = assemble_shared_context_material(text, latest_memories=latest_memories)
            return project_classification_context(material)

        # Fallback path (kept for compatibility)
        import re
        links = re.findall(r"https?://\\S+", text)
        files = re.findall(r"\\b[\\w./-]+\\.(?:py|js|ts|md|yaml|yml|json|toml|sql|sh)\\b", text)
        return {
            "has_code": "```" in text or "[CODE_BLOCK]" in text,
            "links": links[:5],
            "files": files[:5],
            "recent_categories": [m.get("category") for m in latest_memories[:5]],
            "recent_tags": sorted({tag for m in latest_memories[:5] for tag in m.get("tags", [])})[:8],
        }
