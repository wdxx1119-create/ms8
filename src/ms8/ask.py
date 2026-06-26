"""Lightweight ask command for no-friction memory use."""

from __future__ import annotations

from .absorb.search import search_chunks
from .absorb.project_memory.search import search_registered_projects
from .runtime import consume_llm_degraded_notice_runtime, search_memories_detailed, write_memory

REMEMBER_PREFIXES = ("记住", "保存", "save", "remember")


def _extract_memory_text(query: str) -> str:
    q = query.strip()
    for prefix in REMEMBER_PREFIXES:
        if q.lower().startswith(prefix.lower()):
            return q[len(prefix) :].strip(" :：")
    return ""


def run_ask(query: str, limit: int = 5) -> int:
    notice = consume_llm_degraded_notice_runtime()
    if bool(notice.get("emit", False)) and str(notice.get("message", "")).strip():
        print(f"[ms8] {notice.get('message')}")

    q = query.strip()
    if not q:
        print("ms8 ask: query cannot be empty")
        return 2

    memory_text = _extract_memory_text(q)
    if memory_text:
        rec = write_memory(memory_text, source="ask")
        print(f"saved memory: {rec['id']}")
        return 0

    memory_search = search_memories_detailed(q, limit=limit)
    matches = memory_search.get("items", []) if isinstance(memory_search.get("items", []), list) else []
    absorb_matches = search_chunks(q, limit=limit)
    remaining = max(0, limit - len(matches) - len(absorb_matches))
    project_matches = search_registered_projects(q, limit=max(limit, remaining or limit))
    print(f"matches: {len(matches) + len(absorb_matches) + len(project_matches)}")
    for idx, m in enumerate(matches[:limit], start=1):
        text = str(m.get("text", "")).replace("\n", " ")[:100]
        print(f"{idx}. [{m.get('source', 'unknown')}] {text}")
    offset = min(len(matches), limit)
    for idx, m in enumerate(absorb_matches[: max(0, limit - offset)], start=offset + 1):
        text = str(m.get("text_preview", "")).replace("\n", " ")[:100]
        print(f"{idx}. [absorb:{m.get('file_type', 'file')}] {text}")
    offset += min(len(absorb_matches), max(0, limit - offset))
    for idx, m in enumerate(project_matches[: max(0, limit - offset)], start=offset + 1):
        text = str(m.get("text_preview", "") or m.get("text", "")).replace("\n", " ")[:100]
        print(f"{idx}. [project_memory:{m.get('project_name', 'unknown')}] {text}")
    if not matches and not absorb_matches and not project_matches:
        print("no match found. Tip: use 'ms8 ask \"记住 xxx\"' to save.")
    return 0
