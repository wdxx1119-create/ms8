from __future__ import annotations

from typing import Dict, List

from app.memory.indexer import MemoryIndexer


class MemorySearch:
    def __init__(self, indexer: MemoryIndexer) -> None:
        self.indexer = indexer

    def query(self, text: str, limit: int = 10) -> List[Dict]:
        return self.indexer.search(text, limit=limit)
