from __future__ import annotations

from typing import Any

_assemble_shared_context_material: Any = None
_project_classification_context: Any = None

try:
    from ms8.engine_core.context_material import (
        assemble_shared_context_material as _asm_impl,
    )
    from ms8.engine_core.context_material import (
        project_classification_context as _proj_impl,
    )
    _assemble_shared_context_material = _asm_impl
    _project_classification_context = _proj_impl
except ImportError as exc:
    print(f"[ContextBuilder] context_material unavailable, using fallback context builder: {exc}")


class ContextBuilder:
    """Build lightweight classification context.

    Scope boundary:
    - This module only projects classification view.
    - Shared raw material is assembled once in memory.context_material.
    - Response-time injection stays in memory.working_memory / MemoryCore.
    """

    def build(self, text: str, latest_memories: list[dict]) -> dict:
        asm = _assemble_shared_context_material
        proj = _project_classification_context
        if callable(asm) and callable(proj):
            material = asm(text, latest_memories=latest_memories)
            return proj(material)

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
