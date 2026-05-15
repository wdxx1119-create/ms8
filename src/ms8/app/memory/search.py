from __future__ import annotations

import warnings

from ms8.app.memory.indexer import MemoryIndexer


class MemorySearch:
    def __init__(self, indexer: MemoryIndexer) -> None:
        self.indexer = indexer

    def query(self, text: str, limit: int = 10) -> list[dict]:
        # Legacy output path kept for compatibility.
        return self.indexer.search(text, limit=limit)

    def query_unified(self, text: str, limit: int = 10) -> list[dict]:
        warnings.warn(
            "MemorySearch.query_unified is deprecated; prefer MemoryCore.retrieve_memories unified outlet.",
            DeprecationWarning,
            stacklevel=2,
        )
        rows = self.indexer.search(text, limit=limit)
        unified: list[dict] = []
        for idx, row in enumerate(rows):
            score = float(row.get("confidence", 0.0) or 0.0)
            unified.append(
                {
                    "id": str(row.get("meta", {}).get("id", "")) or f"idx-{idx}",
                    "source": str(row.get("source", "auto_memory_index")),
                    "title": str(row.get("category", "memory")),
                    "content": str(row.get("text", row.get("normalized_text", ""))),
                    "date": str(row.get("created_at", "")),
                    "scores": {
                        "lexical": score,
                        "semantic": 0.0,
                        "graph": 0.0,
                        "fusion": score,
                        "trust": score,
                        "rerank": score,
                    },
                    "signals": {
                        "search_type": "incremental",
                        "matched_entities": row.get("entities", []),
                    },
                    "raw": row,
                }
            )
        return unified
