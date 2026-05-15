"""
Memory governance checks for dedupe, conflicts, and staleness.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .config import get_config
from .metrics_contract import metric_contract


class MemoryGovernance:
    """Track governance metadata around memory writes and search results."""

    def __init__(self) -> None:
        self.config = get_config()
        self.file = self.config["memory_dir"] / "governance_report.json"
        gov_cfg = self.config["settings"]["memory"].get("governance", {})
        trust_cfg = gov_cfg.get("trust_scoring", {})
        self.base_score = float(trust_cfg.get("base_score", 0.5))
        self.memory_md_bonus = float(trust_cfg.get("memory_md_bonus", 0.35))
        self.daily_log_bonus = float(trust_cfg.get("daily_log_bonus", 0.2))
        self.default_source_bonus = float(trust_cfg.get("default_source_bonus", 0.1))
        self.stale_penalty = float(trust_cfg.get("stale_penalty", 0.2))
        self.semantic_overlap_threshold = float(gov_cfg.get("semantic_overlap_threshold", 0.55))
        self.state = self._load()

    def _load(self) -> Dict:
        if self.file.exists():
            try:
                with open(self.file, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                pass
        return {"entries": [], "alerts": []}

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=2, ensure_ascii=False)

    def _normalize(self, text: str) -> str:
        return " ".join(text.lower().split())

    def _extract_subject(self, text: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9_]{3,}", text.lower())
        return " ".join(tokens[:6])

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[A-Za-z0-9_]{3,}", text.lower())

    def _semantic_overlap(self, left: str, right: str) -> float:
        left_tokens = set(self._tokenize(left))
        right_tokens = set(self._tokenize(right))
        if not left_tokens or not right_tokens:
            return 0.0
        intersection = len(left_tokens & right_tokens)
        return intersection / math.sqrt(len(left_tokens) * len(right_tokens))

    def _trust_score(self, source: str, stale: bool) -> float:
        score = self.base_score
        if source == "MEMORY.md":
            score += self.memory_md_bonus
        elif source.startswith("daily_log:"):
            score += self.daily_log_bonus
        else:
            score += self.default_source_bonus
        if stale:
            score -= self.stale_penalty
        return max(0.0, min(1.0, score))

    def assess_memory_write(self, content: str, existing_blocks: Dict[str, str], extra_sources: Optional[Dict[str, str]] = None) -> Dict:
        normalized = self._normalize(content)
        subject = self._extract_subject(content)
        extra_sources = extra_sources or {}
        positive_polarity = any(word in normalized for word in ["prefer", "prefers", "always", "likes"])
        negative_polarity = any(word in normalized for word in ["avoid", "never", "dislike"])

        duplicate_in = []
        conflicts = []
        semantic_conflicts = []
        combined_sources = dict(existing_blocks)
        combined_sources.update(extra_sources)
        for block_name, block_content in combined_sources.items():
            block_norm = self._normalize(block_content)
            block_positive = any(word in block_norm for word in ["prefer", "prefers", "always", "likes"])
            block_negative = any(word in block_norm for word in ["avoid", "never", "dislike"])
            if normalized and normalized in block_norm:
                duplicate_in.append(block_name)
            if subject and subject in block_norm:
                if positive_polarity and block_negative:
                    conflicts.append(block_name)
                if negative_polarity and block_positive:
                    conflicts.append(block_name)
                if ("always" in normalized and "never" in block_norm) or ("never" in normalized and "always" in block_norm):
                    conflicts.append(block_name)
            overlap = self._semantic_overlap(content, block_content)
            if overlap >= self.semantic_overlap_threshold and ((positive_polarity and block_negative) or (negative_polarity and block_positive)):
                conflicts.append(block_name)
            if overlap >= self.semantic_overlap_threshold and block_name not in duplicate_in and block_name not in conflicts:
                semantic_conflicts.append({
                    "source": block_name,
                    "overlap": round(overlap, 3),
                })

        assessment = {
            "content": content,
            "subject": subject,
            "is_duplicate": bool(duplicate_in),
            "duplicate_blocks": duplicate_in,
            "has_conflict": bool(conflicts),
            "conflict_blocks": conflicts,
            "semantic_conflicts": semantic_conflicts,
            "timestamp": datetime.now().isoformat(),
        }
        self.state["entries"].append(assessment)
        if assessment["is_duplicate"] or assessment["has_conflict"] or assessment["semantic_conflicts"]:
            self.state["alerts"].append(assessment)
        self._save()
        return assessment

    def annotate_search_result(self, result: Dict) -> Dict:
        date = result.get("date")
        stale = False
        if isinstance(date, datetime):
            stale = date < datetime.now() - timedelta(days=45)

        content = str(result.get("content", ""))
        normalized = self._normalize(content)
        duplicate_count = 0
        for entry in self.state.get("entries", []):
            if entry.get("content") and self._normalize(entry["content"]) in normalized:
                duplicate_count += 1

        annotated = dict(result)
        annotated["governance"] = {
            "stale": stale,
            "duplicate_mentions": duplicate_count,
            "trust_score": self._trust_score(str(result.get("source", "")), stale),
            "recent_alerts": self.state.get("alerts", [])[-3:],
        }
        return annotated

    def report(self, limit: int = 20) -> Dict:
        return {
            "entries": self.state.get("entries", [])[-limit:],
            "alerts": self.state.get("alerts", [])[-limit:],
            "core_metric_contract": metric_contract(),
        }
