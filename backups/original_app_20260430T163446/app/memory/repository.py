from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.schemas.pipeline_schema import MemoryRecord
from memory.file_write_guard import secure_append_text, secure_read_text, secure_write_text


class MemoryRepository:
    def __init__(self, store_path: Path) -> None:
        self.store_path = store_path
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.store_path.exists():
            secure_write_text(self.store_path, "")

    @staticmethod
    def _parse_iso_utc(raw: str) -> datetime | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        except Exception:
            return None

    def list_recent(self, limit: int = 20) -> List[Dict]:
        rows: List[Dict] = []
        for line in secure_read_text(self.store_path).splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:][::-1]

    def find_duplicate(self, dedupe_key: str) -> Optional[str]:
        for item in self.list_recent(limit=500):
            if item.get("meta", {}).get("dedupe_key") == dedupe_key:
                return str(item.get("meta", {}).get("id", ""))
        return None

    def find_duplicates(self, dedupe_key: str, limit: int = 500) -> List[Dict]:
        out: List[Dict] = []
        for item in self.list_recent(limit=limit):
            if item.get("meta", {}).get("dedupe_key") == dedupe_key:
                out.append(item)
        return out

    def find_recent_duplicates(self, dedupe_key: str, within_minutes: int = 5, limit: int = 500) -> List[Dict]:
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(minutes=max(1, within_minutes))
        out: List[Dict] = []
        for item in self.find_duplicates(dedupe_key, limit=limit):
            created_at = str(item.get("created_at", ""))
            ts = self._parse_iso_utc(created_at)
            if ts is None:
                continue
            if ts >= threshold:
                out.append(item)
        return out

    def find_recent_by_category(self, category: str, within_minutes: int = 60, limit: int = 300) -> List[Dict]:
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(minutes=max(1, within_minutes))
        out: List[Dict] = []
        for item in self.list_recent(limit=limit):
            if str(item.get("category", "")) != str(category):
                continue
            created_at = str(item.get("created_at", ""))
            ts = self._parse_iso_utc(created_at)
            if ts is None:
                continue
            if ts >= threshold:
                out.append(item)
        return out

    def save(self, record: MemoryRecord) -> Dict:
        payload = {
            "id": str(record.meta.get("id", "")),
            "text": record.text,
            "normalized_text": record.normalized_text,
            "category": record.category,
            "confidence": record.confidence,
            "tags": record.tags,
            "entities": record.entities,
            "action": record.action,
            "object": record.object,
            "status": record.status,
            "time_info": record.time_info,
            "matched_rules": record.matched_rules,
            "llm_used": record.llm_used,
            "needs_review": record.needs_review,
            "review_reason": record.review_reason,
            "duplicate_of": record.duplicate_of,
            "conflict_flag": record.conflict_flag,
            "source": record.source,
            "created_at": record.created_at,
            "meta": record.meta,
        }
        secure_append_text(self.store_path, json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def cleanup(self, excluded_source_prefixes: List[str] | None = None, drop_rejected: bool = True) -> Dict:
        rows = secure_read_text(self.store_path).splitlines()
        kept: List[str] = []
        before = 0
        removed_rejected = 0
        removed_source = 0
        prefixes = [str(x).strip().lower() for x in (excluded_source_prefixes or []) if str(x).strip()]
        for line in rows:
            if not line.strip():
                continue
            before += 1
            try:
                row = json.loads(line)
            except Exception:
                continue
            source = str(row.get("source", "")).lower()
            if any(source.startswith(prefix) for prefix in prefixes):
                removed_source += 1
                continue
            if drop_rejected and str(row.get("status", "")).lower() == "rejected":
                removed_rejected += 1
                continue
            kept.append(json.dumps(row, ensure_ascii=False))
        secure_write_text(self.store_path, "\n".join(kept) + ("\n" if kept else ""))
        return {
            "before": before,
            "after": len(kept),
            "removed_source": removed_source,
            "removed_rejected": removed_rejected,
        }
