from __future__ import annotations

import atexit
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from memory.file_write_guard import secure_append_text, secure_read_text, secure_write_text


class MemoryIndexer:
    """Incremental indexer; new records are immediately queryable."""

    def __init__(
        self,
        index_path: Path,
        hot_min_confidence: float = 0.65,
        excluded_source_prefixes: list[str] | None = None,
    ) -> None:
        self.index_path = index_path
        self.journal_path = index_path.with_suffix(index_path.suffix + ".journal.jsonl")
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index: List[Dict] = []
        self._hot_index: List[Dict] = []
        self._cold_index: List[Dict] = []
        self._container_mode = "list"
        self.hot_days = 30
        self.hot_min_confidence = float(hot_min_confidence)
        self.excluded_source_prefixes = [str(x).strip().lower() for x in (excluded_source_prefixes or []) if str(x).strip()]
        self._dirty_writes = 0
        # Keep on-disk index immediately available by default.
        self._flush_every = 1
        atexit.register(self.flush)
        if self.index_path.exists():
            try:
                loaded = json.loads(secure_read_text(self.index_path) or "[]")
                if isinstance(loaded, list):
                    self._index = loaded
                    self._container_mode = "list"
                elif isinstance(loaded, dict):
                    items = loaded.get("items", [])
                    hot_items = loaded.get("hot_items", [])
                    cold_items = loaded.get("cold_items", [])
                    if isinstance(items, list):
                        self._index = items
                        self._container_mode = "dict"
                        if isinstance(hot_items, list):
                            self._hot_index = hot_items
                        if isinstance(cold_items, list):
                            self._cold_index = cold_items
                    else:
                        self._index = []
                else:
                    self._index = []
            except Exception:
                self._index = []
        if self._container_mode == "list" and self._index:
            self._rebuild_hot_cold()
        elif self._container_mode == "dict" and self._index:
            self._rebuild_hot_cold()
        self._replay_journal()

    def _is_excluded_source(self, memory: Dict) -> bool:
        source = str(memory.get("source", "")).strip().lower()
        text = str(memory.get("text", "")).lower()
        return any(source.startswith(prefix) for prefix in self.excluded_source_prefixes) or ("样本" in text and source.startswith("verify_canary"))

    def _rebuild_hot_cold(self) -> None:
        self._hot_index = []
        self._cold_index = []
        for item in self._index:
            if self._is_hot(item):
                self._hot_index.append(item)
            else:
                self._cold_index.append(item)

    def _is_hot(self, memory: Dict) -> bool:
        conf = float(memory.get("confidence", 0.0) or 0.0)
        created = str(memory.get("created_at", ""))
        if conf < self.hot_min_confidence:
            return False
        try:
            raw = created.strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            ts = datetime.fromisoformat(raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
        except Exception:
            return False
        return datetime.now(timezone.utc) - ts <= timedelta(days=self.hot_days)

    def _dump_payload(self) -> Dict | List:
        if self._container_mode == "dict":
            return {
                "items": self._index[-5000:],
                "hot_items": self._hot_index[-4000:],
                "cold_items": self._cold_index[-4000:],
            }
        return self._index[-5000:]

    def _append_journal(self, memory: Dict) -> None:
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            secure_append_text(self.journal_path, json.dumps(memory, ensure_ascii=False) + "\n")
        except Exception:
            return

    def _replay_journal(self) -> None:
        if not self.journal_path.exists():
            return
        try:
            for line in secure_read_text(self.journal_path).splitlines():
                if not line.strip():
                    continue
                try:
                    memory = json.loads(line)
                except Exception:
                    continue
                if not isinstance(memory, dict):
                    continue
                if self._is_excluded_source(memory):
                    continue
                self._index.append(memory)
                if self._is_hot(memory):
                    self._hot_index.append(memory)
                else:
                    self._cold_index.append(memory)
        except Exception:
            return

    def _persist(self, force: bool = False) -> None:
        self._dirty_writes += 1
        if not force and self._dirty_writes < self._flush_every:
            return
        try:
            payload = json.dumps(self._dump_payload(), ensure_ascii=False)
            secure_write_text(self.index_path, payload)
            if self.journal_path.exists():
                secure_write_text(self.journal_path, "")
            self._dirty_writes = 0
        except Exception:
            # Keep in-memory index usable even when backing file is temporarily read-only/unavailable.
            return

    def flush(self) -> None:
        self._persist(force=True)

    def add(self, memory: Dict) -> None:
        if self._is_excluded_source(memory):
            return
        self._index.append(memory)
        if self._is_hot(memory):
            self._hot_index.append(memory)
        else:
            self._cold_index.append(memory)
        self._append_journal(memory)
        self._persist(force=False)

    def cleanup_excluded(self) -> Dict:
        before = len(self._index)
        self._index = [m for m in self._index if not self._is_excluded_source(m)]
        self._rebuild_hot_cold()
        self._persist(force=True)
        return {"before": before, "after": len(self._index), "removed": max(0, before - len(self._index))}

    def cleanup_rejected(self) -> Dict:
        before = len(self._index)
        self._index = [m for m in self._index if str(m.get("status", "")).lower() != "rejected"]
        self._rebuild_hot_cold()
        self._persist(force=True)
        return {"before": before, "after": len(self._index), "removed": max(0, before - len(self._index))}

    def _tokenize(self, text: str) -> List[str]:
        lowered = str(text or "").lower().strip()
        if not lowered:
            return []
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", lowered)
        latin_words = re.findall(r"[a-z0-9_]+", lowered)
        cjk_bigrams = ["".join(cjk_chars[i : i + 2]) for i in range(len(cjk_chars) - 1)]
        return [x for x in (cjk_chars + cjk_bigrams + latin_words) if x]

    def _hit_score(self, query: str, memory: Dict) -> float:
        text = str(memory.get("normalized_text", "")).lower()
        if not text:
            return 0.0
        if query in text:
            return 1.0
        q_tokens = self._tokenize(query)
        if not q_tokens:
            return 0.0
        matched = sum(1 for tok in q_tokens if tok and tok in text)
        if matched == 0:
            return 0.0
        return matched / max(1, len(q_tokens))

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        q = query.lower().strip()
        if not q:
            return []
        hot_hits = []
        for m in reversed(self._hot_index):
            score = self._hit_score(q, m)
            if score > 0:
                hot_hits.append((score, m))
        hot_hits.sort(key=lambda x: x[0], reverse=True)
        if len(hot_hits) >= limit:
            return [m for _, m in hot_hits[:limit]]
        cold_hits = []
        for m in reversed(self._cold_index):
            score = self._hit_score(q, m)
            if score > 0:
                cold_hits.append((score, m))
        cold_hits.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in (hot_hits + cold_hits)[:limit]]
