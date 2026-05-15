"""Lightweight ask command for no-friction memory use."""

from __future__ import annotations

from .runtime import consume_llm_degraded_notice_runtime, search_memories, write_memory

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

    matches = search_memories(q)
    print(f"matches: {len(matches)}")
    for idx, m in enumerate(matches[:limit], start=1):
        text = str(m.get("text", "")).replace("\n", " ")[:100]
        print(f"{idx}. [{m.get('source', 'unknown')}] {text}")
    if not matches:
        print("no match found. Tip: use 'ms8 ask \"记住 xxx\"' to save.")
    return 0
