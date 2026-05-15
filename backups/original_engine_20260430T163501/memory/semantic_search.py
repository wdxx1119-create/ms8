"""
Lightweight semantic recall for memory documents.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .config import get_config
from .file_write_guard import atomic_write_json
from .file_store import FileMemoryStore
from .utils import list_daily_log_files

try:
    import ollama
except ImportError:
    ollama = None


class SemanticMemorySearch:
    """Provide semantic-style recall with embedding fallback."""

    def __init__(self) -> None:
        self.config = get_config()
        self.file_store = FileMemoryStore()
        self.cache_file = self.config["memory_dir"] / "semantic_cache.json"
        self.embedding_model = os.environ.get("OPENCLAW_MEMORY_EMBED_MODEL", "nomic-embed-text:latest")
        self.failure_retry_ttl_seconds = int(os.environ.get("OPENCLAW_MEMORY_EMBED_RETRY_TTL_SECONDS", "21600"))
        self.max_failure_retries = int(os.environ.get("OPENCLAW_MEMORY_EMBED_MAX_RETRIES", "8"))
        self._cache = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict]:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                return {}
        return {}

    def _save_cache(self) -> None:
        atomic_write_json(self.cache_file, self._cache, ensure_ascii=False, indent=2)

    def _tokenize(self, text: str) -> List[str]:
        tokens: List[str] = []
        lowered = text.lower()
        tokens.extend(re.findall(r"[A-Za-z0-9_]{2,}", lowered))
        cjk = re.findall(r"[\u4e00-\u9fff]", text)
        if cjk:
            # Chinese fallback sparse: char + bi-gram to avoid full miss on no-space queries.
            tokens.extend(cjk)
            tokens.extend(["".join(cjk[i : i + 2]) for i in range(len(cjk) - 1)])
        return tokens

    def _sparse_vector(self, text: str) -> Dict[str, float]:
        counts: Dict[str, float] = {}
        for token in self._tokenize(text):
            counts[token] = counts.get(token, 0.0) + 1.0
        norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
        return {token: value / norm for token, value in counts.items()}

    def _cosine_sparse(self, left: Dict[str, float], right: Dict[str, float]) -> float:
        if len(left) > len(right):
            left, right = right, left
        return sum(value * right.get(token, 0.0) for token, value in left.items())

    def _ollama_embedding(self, text: str) -> Optional[List[float]]:
        host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        if ollama is not None:
            try:
                client = ollama.Client(host=host, trust_env=False)
                response = client.embeddings(model=self.embedding_model, prompt=text)
                return [float(value) for value in response["embedding"]]
            except Exception:
                pass
        # Fallback: call Ollama HTTP API directly, so embeddings still work without python ollama package.
        try:
            url = host.rstrip("/") + "/api/embeddings"
            body = json.dumps({"model": self.embedding_model, "prompt": text}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            emb = payload.get("embedding", [])
            if isinstance(emb, list) and emb:
                return [float(v) for v in emb]
        except Exception:
            return None
        return None

    def _cosine_dense(self, left: List[float], right: List[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
        right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
        return dot / (left_norm * right_norm)

    def _embed_or_sparse(self, key: str, text: str, force: bool = False) -> Dict:
        cached = self._cache.get(key)
        if cached and not force and not self._should_retry_dense(cached):
            return cached

        dense = self._ollama_embedding(text)
        payload = {
            "text": text[:500],
            "dense": dense,
            "sparse": self._sparse_vector(text),
            "updated_at": datetime.now().isoformat(),
            "retry_count": int((cached or {}).get("retry_count", 0)),
            "last_error": str((cached or {}).get("last_error", "")),
        }
        if dense is None:
            payload["retry_count"] = int(payload.get("retry_count", 0)) + 1
            payload["last_error"] = "embedding_unavailable"
        else:
            payload["retry_count"] = 0
            payload["last_error"] = ""
        self._cache[key] = payload
        self._save_cache()
        return payload

    def _should_retry_dense(self, cached: Dict) -> bool:
        if cached.get("dense") is not None:
            return False
        retry_count = int(cached.get("retry_count", 0) or 0)
        if retry_count >= max(1, self.max_failure_retries):
            return False
        raw = str(cached.get("updated_at", "") or "")
        if not raw:
            return True
        try:
            ts = datetime.fromisoformat(raw)
            return (datetime.now() - ts).total_seconds() >= max(60, self.failure_retry_ttl_seconds)
        except Exception:
            return True

    def repair_missing_dense(self, limit: int = 80, include_queries: bool = False) -> Dict:
        keys = list(self._cache.keys())
        repaired = 0
        checked = 0
        for key in keys:
            if not include_queries and str(key).startswith("query::"):
                continue
            item = self._cache.get(key, {})
            if item.get("dense") is not None:
                continue
            checked += 1
            self._embed_or_sparse(key, str(item.get("text", "")), force=True)
            if (self._cache.get(key) or {}).get("dense") is not None:
                repaired += 1
            if checked >= max(1, int(limit)):
                break
        return {"checked": checked, "repaired": repaired, "remaining_missing": sum(1 for v in self._cache.values() if isinstance(v, dict) and v.get("dense") is None)}

    def _documents(self) -> List[Dict]:
        docs: List[Dict] = []
        memory_md = self.file_store.read_memory_md()
        docs.append({
            "id": "MEMORY.md",
            "source": "MEMORY.md",
            "title": "Long-term Memory",
            "content": memory_md,
            "date": datetime.now(),
        })

        for log_file in list_daily_log_files(self.config["memory_dir"], self.config.get("daily_dir")):
            try:
                with open(log_file, "r", encoding="utf-8") as handle:
                    content = handle.read()
                try:
                    doc_date = datetime.fromisoformat("-".join(log_file.stem.split("-")[0:3]))
                except ValueError:
                    doc_date = datetime.fromtimestamp(log_file.stat().st_mtime)
                docs.append({
                    "id": f"daily_log:{log_file.name}",
                    "source": f"daily_log:{log_file.name}",
                    "title": f"Daily Log - {log_file.stem}",
                    "content": content,
                    "date": doc_date,
                })
            except Exception:
                continue
        return docs

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        docs = self._documents()
        query_payload = self._embed_or_sparse(f"query::{query}", query)
        matches: List[Dict] = []

        for doc in docs:
            doc_payload = self._embed_or_sparse(doc["id"], doc["content"])
            dense_score = 0.0
            sparse_score = self._cosine_sparse(query_payload["sparse"], doc_payload["sparse"])
            if query_payload["dense"] and doc_payload["dense"]:
                dense_score = self._cosine_dense(query_payload["dense"], doc_payload["dense"])
            score = max(dense_score, sparse_score)
            if score <= 0 and query.strip() and query in str(doc.get("content", "")):
                score = 0.05
            if score <= 0:
                q_tokens = self._tokenize(query)
                if q_tokens:
                    doc_text = str(doc.get("content", ""))
                    hit = sum(1 for tk in q_tokens if tk and tk in doc_text)
                    if hit > 0:
                        score = min(0.09, 0.04 + hit * 0.01)
            if score <= 0:
                continue
            matches.append({
                "content": doc["content"],
                "source": doc["source"],
                "date": doc["date"],
                "score": score,
                "title": doc["title"],
                "search_type": "semantic",
            })

        matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[:top_k]
