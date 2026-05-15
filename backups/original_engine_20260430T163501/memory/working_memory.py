"""Working-memory utilities: persistence, context injection, and usage tracking."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from .file_write_guard import secure_append_text, secure_read_text, secure_write_text


class WorkingMemoryManager:
    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        mem_cfg = config["settings"]["memory"].get("working_memory", {})
        self.enabled = bool(mem_cfg.get("enabled", True))
        self.max_restore = int(mem_cfg.get("max_restore", 120))
        self.injection_top_k = int(mem_cfg.get("injection_top_k", 5))
        self.max_injection_chars = int(mem_cfg.get("max_injection_chars", 1800))
        ranking_cfg = mem_cfg.get("ranking_weights", {})
        self.rank_score_w = float(ranking_cfg.get("score", 0.45))
        self.rank_recency_w = float(ranking_cfg.get("recency", 0.25))
        self.rank_conf_w = float(ranking_cfg.get("confidence", 0.20))
        self.rank_overlap_w = float(ranking_cfg.get("overlap", 0.10))
        self.topic_overlap_w = float(ranking_cfg.get("topic_overlap", 0.50))
        self.topic_importance_w = float(ranking_cfg.get("topic_importance", 0.50))
        self.recency_cfg = mem_cfg.get("recency_scoring", {})
        self.test_filter_cfg = mem_cfg.get("test_filter", {})
        # Keep behavior stable even if custom weights do not sum to 1.
        rank_sum = self.rank_score_w + self.rank_recency_w + self.rank_conf_w + self.rank_overlap_w
        if rank_sum <= 0:
            rank_sum = 1.0
        self.rank_score_w /= rank_sum
        self.rank_recency_w /= rank_sum
        self.rank_conf_w /= rank_sum
        self.rank_overlap_w /= rank_sum
        topic_sum = self.topic_overlap_w + self.topic_importance_w
        if topic_sum <= 0:
            topic_sum = 1.0
        self.topic_overlap_w /= topic_sum
        self.topic_importance_w /= topic_sum

        base = config["workspace_dir"]
        persistence_raw = mem_cfg.get("persistence_file", "memory/working_memory.jsonl")
        usage_raw = mem_cfg.get("usage_log_file", "memory/memory_usage_log.jsonl")
        self.persistence_file = self._resolve(base, persistence_raw)
        self.usage_log_file = self._resolve(base, usage_raw)

        self.persistence_file.parent.mkdir(parents=True, exist_ok=True)
        self.usage_log_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.persistence_file.exists():
            secure_write_text(self.persistence_file, "")
        if not self.usage_log_file.exists():
            secure_write_text(self.usage_log_file, "")

    def _resolve(self, base: Path, raw: str) -> Path:
        path = Path(raw).expanduser()
        return path if path.is_absolute() else base / path

    def _append_jsonl(self, file_path: Path, payload: Dict[str, Any]) -> None:
        secure_append_text(file_path, json.dumps(payload, ensure_ascii=False) + "\n")

    def append_item(self, content: str, importance: float, topic: str, source: str = "interaction") -> None:
        if not self.enabled:
            return
        if self._looks_like_test_content(content):
            return
        payload = {
            "timestamp": self._utc_now().isoformat(),
            "content": content,
            "importance": round(float(importance), 3),
            "topic": topic,
            "source": source,
        }
        self._append_jsonl(self.persistence_file, payload)

    def _looks_like_test_content(self, content: str) -> bool:
        if not bool(self.test_filter_cfg.get("enabled", True)):
            return False
        text = str(content or "").lower()
        keywords = [str(x).lower() for x in self.test_filter_cfg.get("keywords", [
            "verification interaction",
            "working_memory_check",
            "监控验证",
            "test_only",
        ])]
        return any(k and k in text for k in keywords)

    def purge_test_rows(self) -> Dict[str, Any]:
        if not self.persistence_file.exists():
            return {"status": "skipped", "reason": "missing_working_memory_file"}
        rows = secure_read_text(self.persistence_file).splitlines()
        kept: List[str] = []
        removed = 0
        for line in rows:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                kept.append(line)
                continue
            blob = f"{row.get('content','')} {row.get('topic','')} {row.get('source','')}"
            if self._looks_like_test_content(blob):
                removed += 1
                continue
            kept.append(json.dumps(row, ensure_ascii=False))
        secure_write_text(self.persistence_file, "\n".join(kept) + ("\n" if kept else ""))
        return {"status": "success", "removed": removed, "remaining": len(kept)}

    def _read_recent_rows(self, limit: int = 3000) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for line in secure_read_text(self.persistence_file).splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:]

    def restore_items(self, max_size: int) -> List[str]:
        if not self.enabled:
            return []
        rows = self._read_recent_rows(limit=max(self.max_restore * 2, max_size * 2))
        return [str(r.get("content", "")) for r in rows[-max_size:] if r.get("content")]

    def restore_by_topic(self, topic_query: str, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        query_tokens = self._tokens(topic_query)
        rows = self._read_recent_rows(limit=2000)
        scored: List[Dict[str, Any]] = []
        for row in rows:
            text = str(row.get("content", ""))
            topic = str(row.get("topic", ""))
            tokens = self._tokens(text + " " + topic)
            overlap = len(query_tokens & tokens)
            if overlap <= 0:
                continue
            score = overlap * self.topic_overlap_w + float(row.get("importance", 0.0)) * self.topic_importance_w
            scored.append({"score": score, **row})
        scored.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return scored[:limit]

    def rank_search_results(self, query: str, results: List[Dict[str, Any]], top_k: int | None = None) -> List[Dict[str, Any]]:
        top_k = top_k or self.injection_top_k
        now = self._utc_now()
        query_tokens = self._tokens(query)

        ranked: List[Dict[str, Any]] = []
        for item in results:
            content = str(item.get("content", ""))
            score = float(item.get("score", item.get("rerank_score", 0.0)) or 0.0)
            confidence = float(item.get("confidence", item.get("governance", {}).get("trust_score", 0.6)) or 0.6)
            recency = self._recency_score(item.get("date", ""), now)
            overlap = self._overlap_ratio(query_tokens, self._tokens(content))
            final = round(
                self.rank_score_w * score
                + self.rank_recency_w * recency
                + self.rank_conf_w * confidence
                + self.rank_overlap_w * overlap,
                4,
            )
            ranked.append({**item, "working_rank": final, "recency_score": recency, "overlap": overlap})

        ranked.sort(key=lambda x: x.get("working_rank", 0.0), reverse=True)
        return ranked[:top_k]

    def build_injection_text(self, ranked_memories: List[Dict[str, Any]], heading: str = "Relevant Memories", max_chars: int | None = None) -> str:
        if not ranked_memories:
            return ""
        lines = [f"## {heading}"]
        char_budget = int(max_chars) if max_chars and int(max_chars) > 0 else self.max_injection_chars
        total_chars = len(lines[0])
        for idx, item in enumerate(ranked_memories, start=1):
            source = item.get("source", "unknown")
            date = item.get("date", "")
            content = str(item.get("content", "")).replace("\n", " ").strip()
            snippet = content[:220]
            row = f"{idx}. [{source} {date}] {snippet}"
            if total_chars + len(row) > char_budget:
                break
            lines.append(row)
            total_chars += len(row)
        return "\n".join(lines)

    def log_usage(
        self,
        query: str,
        used_memories: List[Dict[str, Any]],
        channel: str = "response_context",
        reason: str = "",
        candidates_count: int = 0,
        topic_hits_count: int = 0,
    ) -> None:
        if not self.enabled:
            return
        injected_count = len(used_memories)
        payload = {
            "timestamp": self._utc_now().isoformat(),
            "channel": channel,
            "query": query,
            "count": injected_count,
            "injected_count": injected_count,
            "used_in_response": bool(injected_count > 0),
            "candidates_count": int(candidates_count),
            "topic_hits_count": int(topic_hits_count),
            "reason": reason or ("injected" if injected_count else "not_injected"),
            "items": [
                {
                    "source": item.get("source", ""),
                    "title": item.get("title", ""),
                    "score": item.get("working_rank", item.get("score", 0.0)),
                }
                for item in used_memories
            ],
        }
        self._append_jsonl(self.usage_log_file, payload)

    def _tokens(self, text: str) -> set[str]:
        return {m.group(0).lower() for m in re.finditer(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]", text or "")}

    def _overlap_ratio(self, left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / max(1, len(left))

    def _recency_score(self, raw_date: str, now: datetime) -> float:
        missing_score = float(self.recency_cfg.get("missing_score", 0.40))
        invalid_score = float(self.recency_cfg.get("invalid_score", 0.40))
        within_1d_score = float(self.recency_cfg.get("within_1d_score", 1.00))
        within_7d_score = float(self.recency_cfg.get("within_7d_score", 0.80))
        within_30d_score = float(self.recency_cfg.get("within_30d_score", 0.60))
        within_90d_score = float(self.recency_cfg.get("within_90d_score", 0.45))
        older_score = float(self.recency_cfg.get("older_score", 0.25))
        if not raw_date:
            return missing_score
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(str(raw_date), fmt)
                dt = dt.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                dt = None
        if dt is None:
            try:
                raw = str(raw_date).strip()
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
            except Exception:
                return invalid_score
        age = now - dt
        if age <= timedelta(days=1):
            return within_1d_score
        if age <= timedelta(days=7):
            return within_7d_score
        if age <= timedelta(days=30):
            return within_30d_score
        if age <= timedelta(days=90):
            return within_90d_score
        return older_score
