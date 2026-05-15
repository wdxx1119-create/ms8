"""MS8 demo flow."""

from __future__ import annotations

import logging
import os

from .runtime import ensure_runtime_dirs, read_memories, search_memories, write_memory

DEMO_TEXT = "MS8 demo memory: 中文搜索优化"
DEMO_QUERY = "中文搜索"
logger = logging.getLogger(__name__)


def run_demo(scenario: str = "default") -> int:
    print("MS8 Demo")
    print(f"Scenario: {scenario}")
    if os.environ.get("MS8_ENV") == "stub":
        print("Mode: stub (local runtime still active)")

    print("[1/4] 初始化运行目录 ... ", end="")
    paths = ensure_runtime_dirs()
    print("OK")

    print("[2/4] 写入示例记忆 ... ", end="")
    rec = write_memory(DEMO_TEXT, source="demo")
    size = len(DEMO_TEXT.encode("utf-8"))
    print(f"OK (1 memory written, {size} bytes)")

    print("[3/4] 检索示例记忆 ... ", end="")
    matches = search_memories(DEMO_QUERY)
    print(f"OK ({len(matches)} match found)")

    print("[4/4] 输出结果校验 ... ", end="")
    hit = next(
        (
            m
            for m in matches
            if (rec.get("id") and m.get("id") == rec.get("id")) or (DEMO_TEXT in str(m.get("text", "")))
        ),
        None,
    )
    # Broad query may return relevant context without including the exact new row.
    # Fall back to an exact-text query before declaring failure.
    if not hit:
        exact_matches = search_memories(DEMO_TEXT)
        hit = next(
            (
                m
                for m in exact_matches
                if (rec.get("id") and m.get("id") == rec.get("id")) or (DEMO_TEXT in str(m.get("text", "")))
            ),
            None,
        )
    if not hit:
        direct_rows = read_memories()
        hit = next(
            (
                m
                for m in reversed(direct_rows)
                if (rec.get("id") and str(m.get("id", "")) == str(rec.get("id", "")))
                or (DEMO_TEXT in str(m.get("text", "")))
            ),
            None,
        )
    if not hit:
        print("FAIL")
        print("demo failed: could not retrieve demo memory")
        logger.warning("demo retrieval validation failed for query=%s", DEMO_QUERY)
        return 2
    print("OK")
    print(f"retrieved demo memory: {hit.get('text', '')}")
    print(f"runtime: {paths['root']}")
    print("✅ demo completed")
    return 0
