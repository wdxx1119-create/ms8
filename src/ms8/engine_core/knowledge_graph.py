"""
Knowledge graph augmentation for the memory runtime.

This module keeps the graph isolated from the core memory path:
- graph extraction failures should never block memory writes
- graph data can be rebuilt from existing memory files
- graph storage lives in a dedicated SQLite database
"""

# Subdomain: knowledge (logical taxonomy; path unchanged)
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import sqlite3
import threading
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import get_config
from .file_write_guard import atomic_write_json
from .local_llm import LLMConfig, LocalLLM
from .utils import list_daily_log_files

ENTITY_TYPES = {
    "person",
    "tool",
    "concept",
    "project",
    "organization",
    "technology",
    "event",
    "resource",
    "configuration",
    "decision",
}

RELATION_TYPES = {
    "depends_on",
    "part_of",
    "uses",
    "similar_to",
    "replaces",
    "creates",
    "belongs_to",
    "related_to",
    "causes",
    "contradicts",
    "evolves_from",
    "learns_from",
}

RELATION_LABELS = {
    "depends_on": "依赖",
    "part_of": "组成",
    "uses": "使用",
    "similar_to": "相似",
    "replaces": "替代",
    "creates": "创建",
    "belongs_to": "归属",
    "related_to": "相关",
    "causes": "导致",
    "contradicts": "矛盾",
    "evolves_from": "演变",
    "learns_from": "学习",
}

ENTITY_TYPE_LABELS = {
    "person": "人物",
    "tool": "工具",
    "concept": "概念",
    "project": "项目",
    "organization": "组织",
    "technology": "技术",
    "event": "事件",
    "resource": "资源",
    "configuration": "配置",
    "decision": "决策",
}

STOP_QUERY_TOKENS = {
    "什么",
    "可以",
    "有",
    "那个",
    "这个",
    "一下",
    "一下子",
    "怎么",
    "如何",
    "然后",
    "以及",
    "还有",
    "一个",
    "一些",
    "东西",
    "有关",
    "关系",
    "相关",
    "问题",
    "是不是",
}

GENERIC_QUERY_TOKENS = {
    "模型",
    "工具",
    "系统",
    "记忆",
    "技能",
    "配置",
    "端口",
    "路径",
}

GENERIC_ENTITY_TERMS = {
    "ai",
    "type",
    "item",
    "value",
    "data",
    "result",
    "text",
    "message",
    "query",
    "response",
    "profile=",
    "现实",
    "只是",
    "第三种可能",
    "另一个可能性",
    "正确的查询方式",
    "专门提取metadata",
    "我的模型",
    "关于我的模型",
    "所有技能",
    "添加模型",
    "保留记忆",
    "配置为第二备用模型",
}

TECH_ENTITY_HINTS = (
    "openclaw",
    "ollama",
    "openrouter",
    "deepseek",
    "qwen",
    "llama",
    "gemma",
    "gpt",
    "sqlite",
    "whoosh",
    "python",
    "github",
    "api",
    "token",
    "memory",
    "graph",
    "检索",
    "图谱",
    "模型",
    "数据库",
    "配置",
    "端口",
    "路径",
    "工作区",
    "技能",
)

STOP_ENTITY_NAMES = {
    "",
    "the",
    "and",
    "for",
    "this",
    "your",
    "curated",
    "freely",
    "edit",
    "memory",
    "long-term",
    "graph",
    "that",
    "with",
    "from",
    "about",
    "there",
    "these",
    "those",
    "用户",
    "系统",
    "问题",
    "功能",
    "东西",
    "情况",
    "内容",
    "方式",
    "说明",
    "支持",
    "memory.md",
}

CHINESE_ENTITY_SUFFIXES = (
    "系统",
    "模型",
    "图谱",
    "记忆",
    "搜索",
    "工具",
    "服务",
    "架构",
    "原则",
    "事件",
    "监控",
    "社区",
    "仓库",
    "配置",
    "端口",
    "路径",
    "策略",
    "项目",
    "教程",
    "文档",
    "网关",
    "技能",
    "框架",
    "数据库",
    "接口",
    "模块",
)


@dataclass
class GraphExtractionResult:
    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    mode: str


class KnowledgeGraph:
    """Knowledge graph storage, extraction, search, and maintenance."""

    def __init__(self, llm: LocalLLM | None = None, llm_config: LLMConfig | None = None):
        self.config = get_config()
        self.settings = self._graph_settings()
        self.quality_cfg = self.config["settings"]["memory"].get("knowledge_graph_quality", {})
        db_path = Path(self.settings["db_path"]).expanduser()
        if not db_path.is_absolute():
            db_path = self.config["workspace_dir"] / db_path
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.quality_state_file = self.config["memory_dir"] / "graph_relation_quality.json"
        self.llm = llm
        self.llm_config = llm_config or LLMConfig()
        self._init_database()
        self._init_quality_state()

    def _q(self, key: str, default: float) -> float:
        return float(self.quality_cfg.get(key, default))

    def _graph_settings(self) -> dict[str, Any]:
        graph_defaults = {
            "enabled": True,
            "db_path": "memory/knowledge_graph.db",
            "extraction_mode": "hybrid",
            "extraction_model": "llama3.2:3b",
            "auto_extract": True,
            "batch_size": 10,
            "daily_decay_rate": 0.05,
            "min_relation_strength": 0.1,
            "duplicate_merge_increment": 0.1,
            "access_increment": 0.05,
            "isolated_entity_retention_days": 30,
            "default_query_depth": 2,
            "max_query_depth": 5,
            "default_return_limit": 10,
            "context_injection_enabled": True,
            "context_injection_limit": 5,
        }
        override = self.config["settings"]["memory"].get("knowledge_graph", {})
        merged = dict(graph_defaults)
        merged.update(override)
        return merged

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_database(self) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    entity_type TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    importance REAL DEFAULT 0.5,
                    access_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_memory_ref TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id INTEGER NOT NULL,
                    alias TEXT NOT NULL,
                    alias_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(entity_id) REFERENCES entities(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject_entity_id INTEGER NOT NULL,
                    object_entity_id INTEGER NOT NULL,
                    relation_type TEXT NOT NULL,
                    strength REAL DEFAULT 0.5,
                    description TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.5,
                    access_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_memory_ref TEXT,
                    UNIQUE(subject_entity_id, object_entity_id, relation_type),
                    FOREIGN KEY(subject_entity_id) REFERENCES entities(id),
                    FOREIGN KEY(object_entity_id) REFERENCES entities(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_anchors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_ref TEXT NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT,
                    memory_hash TEXT NOT NULL,
                    entity_id INTEGER,
                    relation_id INTEGER,
                    snippet TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(entity_id) REFERENCES entities(id),
                    FOREIGN KEY(relation_id) REFERENCES relations(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_extractions (
                    memory_ref TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT,
                    memory_hash TEXT NOT NULL,
                    extractor_mode TEXT NOT NULL,
                    extracted_at TEXT NOT NULL
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_importance ON entities(importance DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_aliases_entity ON entity_aliases(entity_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject_entity_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object_entity_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_strength ON relations(strength DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_anchors_memory_ref ON memory_anchors(memory_ref)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_anchors_entity ON memory_anchors(entity_id)")
            conn.commit()

    def _run_async(self, value: Any) -> Any:
        if not asyncio.iscoroutine(value):
            return value
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)

        result_holder: dict[str, Any] = {"result": None, "error": None}

        def runner() -> None:
            try:
                result_holder["result"] = asyncio.run(value)
            except (RuntimeError, OSError, TypeError, ValueError) as exc:
                result_holder["error"] = str(exc)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if result_holder["error"] is not None:
            raise RuntimeError(str(result_holder["error"]))
        return result_holder["result"]

    def _run_with_timeout(self, func: Any, timeout_seconds: float) -> Any:
        result_holder: dict[str, Any] = {"result": None, "error": None}

        def runner() -> None:
            try:
                result_holder["result"] = func()
            except (RuntimeError, OSError, TypeError, ValueError) as exc:
                result_holder["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join(timeout_seconds)
        if thread.is_alive():
            return None
        if result_holder["error"] is not None:
            raise result_holder["error"]
        return result_holder["result"]

    def _init_quality_state(self) -> None:
        self.quality_state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.quality_state_file.exists():
            self.quality_state_file.write_text(
                json.dumps(
                    {"relations": {}, "last_scan_at": "", "cleanup_reports": []},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    def _load_quality_state(self) -> dict[str, Any]:
        self._init_quality_state()
        try:
            obj = json.loads(self.quality_state_file.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                return {"relations": {}, "last_scan_at": "", "cleanup_reports": []}
            obj.setdefault("relations", {})
            obj.setdefault("last_scan_at", "")
            obj.setdefault("cleanup_reports", [])
            return obj
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {"relations": {}, "last_scan_at": "", "cleanup_reports": []}

    def _save_quality_state(self, state: dict[str, Any]) -> None:
        state["last_scan_at"] = self._utcnow()
        atomic_write_json(self.quality_state_file, state, ensure_ascii=False, indent=2)

    def _relation_quality_status(self, row: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = state or self._load_quality_state()
        rid = str(row.get("id", ""))
        manual = (state.get("relations", {}) or {}).get(rid, {})
        manual_status = str(manual.get("relation_status", "")).strip()
        if manual_status in {"stable", "suspect", "noisy", "archived_candidate"}:
            relation_status = manual_status
            manual_reasons = list(manual.get("reasons", []))
            soft_isolated = bool(
                manual.get("soft_isolated", relation_status in {"suspect", "noisy", "archived_candidate"})
            )
            return {
                "relation_status": relation_status,
                "status_reasons": manual_reasons,
                "soft_isolated": soft_isolated,
            }

        reasons: list[str] = []
        conf = float(row.get("confidence", 0.0) or 0.0)
        access = int(row.get("access_count", 0) or 0)
        source_ref = str(row.get("source_memory_ref", "") or "").strip()
        rel_type = str(row.get("relation_type", ""))
        desc = str(row.get("description", "") or "")
        updated_at = str(row.get("updated_at", "") or "")

        low_conf = conf < self._q("relation_low_confidence", 0.45)
        if low_conf:
            reasons.append("low_confidence")
        if access <= 0:
            reasons.append("low_evidence_count")
        if not source_ref:
            reasons.append("missing_source_path")
        if rel_type == "related_to":
            reasons.append("ambiguous_relation_type")
        if "2-hop inferred" in desc and conf < self._q("two_hop_low_confidence", 0.6):
            reasons.append("low_quality_template_source")
        try:
            dt = self._parse_utc_datetime(updated_at)
            age_days = (datetime.now(timezone.utc) - dt).days if dt is not None else 0
        except (TypeError, ValueError):
            age_days = 0
        long_unused_days = int(self._q("relation_long_unused_days", 60.0))
        long_unused_max_access = int(self._q("relation_long_unused_max_access", 1.0))
        if age_days >= long_unused_days and access <= long_unused_max_access:
            reasons.append("long_time_unused")

        if conf < self._q("relation_reject_confidence", 0.30) or (low_conf and len(reasons) >= 2) or len(reasons) >= 4:
            relation_status = "noisy"
        elif low_conf or len(reasons) >= 2:
            relation_status = "suspect"
        else:
            relation_status = "stable"

        soft_isolated = relation_status in {"suspect", "noisy", "archived_candidate"}
        return {
            "relation_status": relation_status,
            "status_reasons": reasons,
            "soft_isolated": soft_isolated,
        }

    def is_enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    def _utcnow(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _parse_utc_datetime(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        try:
            raw = str(value).strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _normalize_name(self, value: str) -> str:
        return re.sub(r"\s+", " ", value.strip()).casefold()

    def _normalize_candidate(self, value: str) -> str:
        value = value.strip().strip("`'\"“”‘’[](){}<>")
        value = re.sub(r"\s+", " ", value)
        return value.strip(" ,，。；;：:")

    def _clean_scan_text(self, text: str) -> str:
        text = re.sub(r"```[\s\S]*?```", " ", text)
        text = re.sub(r"`[^`]+`", " ", text)
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
        text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
        text = re.sub(r"(^|\n)\s{0,3}#{1,6}\s*", " ", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"\|", " ", text)
        text = re.sub(r"_{2,}", " ", text)
        text = re.sub(r"-{3,}", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _prepare_text_for_sentence_split(self, text: str) -> str:
        text = re.sub(r"```[\s\S]*?```", "\n", text)
        text = re.sub(r"`[^`]+`", " ", text)
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
        text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
        text = re.sub(r"(^|\n)\s{0,3}#{1,6}\s*", "\n", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"\|", " ", text)
        text = re.sub(r"_{2,}", " ", text)
        text = re.sub(r"-{3,}", "\n", text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{2,}", "\n", text)
        return text.strip()

    def _looks_like_noise(self, value: str) -> bool:
        normalized = self._normalize_name(value)
        if normalized in GENERIC_ENTITY_TERMS:
            return True
        if re.fullmatch(r"[A-Za-z]+", value) and value.lower() not in TECH_ENTITY_HINTS and not value[:1].isupper():
            return True
        if re.search(r"\b(?:mon|tue|wed|thu|fri|sat|sun)\b", normalized) and re.search(r"\d{4}-\d{2}-\d{2}", value):
            return True
        if re.search(r"\d{4}-\d{2}-\d{2}", value) or re.search(r"\b\d{1,2}:\d{2}\b", value) or "gmt" in normalized:
            return True
        if any(token in value for token in ("##", "**", "|------|", "<!--")):
            return True
        if re.fullmatch(
            r"(?:和.+模型|根据(?:当前)?配置|作为第?[一二三四五六七八九十0-9]+备用模型|涉及模型|处理模型)",
            value,
        ):
            return True
        if normalized.startswith(
            (
                "和",
                "与",
                "及",
                "并",
                "根据",
                "作为",
                "涉及",
                "这种",
                "说明",
                "不仅",
                "开展",
                "成功",
                "当前状态",
                "内容",
                "处理",
                "关于",
                "我的",
                "所有",
                "添加",
                "保留",
            )
        ) and not any(hint in normalized for hint in TECH_ENTITY_HINTS):
            return True
        if any(
            fragment in normalized
            for fragment in (
                "这是一篇关于",
                "goal anchor",
                "refresh browser",
                "assistant:",
                "set as",
            )
        ):
            return True
        if "ctrl+" in normalized or "cmd+" in normalized or "alt+" in normalized:
            return True
        if value.endswith("/") and not value.startswith(("http://", "https://")):
            return True
        if re.fullmatch(r"\d+(?:\.\d+)+", value):
            return True
        if any(token in value for token in ("profile=", "memory/", "/Users/", "C:\\", "C:/")):
            return True
        if (
            len(value) <= 8
            and any(term in value for term in ("可能", "现实", "方式", "只是", "这个", "那个", "某种"))
            and not any(hint in normalized for hint in TECH_ENTITY_HINTS)
        ):
            return True
        return False

    def _entity_quality_score(self, value: str) -> float:
        normalized = self._normalize_name(value)
        score = 0.0
        if not self._is_meaningful_name(value) or self._looks_like_noise(value):
            return 0.0
        if value.startswith(("http://", "https://")):
            score += self._q("entity_quality_url_bonus", 0.9)
        if any(hint in normalized for hint in TECH_ENTITY_HINTS):
            score += self._q("entity_quality_tech_hint_bonus", 0.55)
        if value.endswith(CHINESE_ENTITY_SUFFIXES):
            score += self._q("entity_quality_suffix_bonus", 0.45)
        if re.search(r"[A-Z][A-Za-z0-9]+", value):
            score += self._q("entity_quality_camelcase_bonus", 0.25)
        if re.search(r"\d", value):
            score += self._q("entity_quality_digit_bonus", 0.15)
        if len(value) >= 3:
            score += self._q("entity_quality_min_len_bonus", 0.1)
        if len(value) > 24:
            score -= self._q("entity_quality_long_len_penalty", 0.15)
        return max(0.0, min(1.0, score))

    def _split_sentences(self, text: str) -> list[str]:
        cleaned = self._prepare_text_for_sentence_split(text)
        return [part.strip() for part in re.split(r"[。！？!?;\n]+", cleaned) if part.strip()]

    def _clean_sentence_for_display(self, text: str) -> str:
        text = self._clean_scan_text(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:180]

    def _compact_entity_description(self, description: str, entity_name: str = "", max_len: int = 120) -> str:
        description = self._clean_sentence_for_display(description)
        if not description:
            return ""
        if entity_name and entity_name in description and len(description) > max_len:
            idx = description.find(entity_name)
            start = max(0, idx - 36)
            end = min(len(description), idx + len(entity_name) + 72)
            description = description[start:end].strip(" ,，。；;：:")
        return description[:max_len].strip()

    def _entity_description_score(self, description: str, entity_name: str = "") -> float:
        description = self._compact_entity_description(description, entity_name=entity_name)
        if not description:
            return self._q("entity_description_empty_score", -1.0)
        normalized = self._normalize_name(description)
        score = 0.0
        if entity_name and entity_name in description:
            score += self._q("entity_description_name_match_bonus", 0.45)
        if any(hint in normalized for hint in TECH_ENTITY_HINTS):
            score += self._q("entity_description_tech_hint_bonus", 0.2)
        if any(
            token in description
            for token in (
                "使用",
                "支持",
                "依赖",
                "集成",
                "调用",
                "配置",
                "访问",
                "部署",
                "运行",
                "创建",
                "导入",
                "切换",
            )
        ):
            score += self._q("entity_description_action_bonus", 0.22)
        if 10 <= len(description) <= 90:
            score += self._q("entity_description_reasonable_len_bonus", 0.12)
        if any(token in description for token in ("分类", "统计", "整理时间", "已完成", "总计", "文件夹")):
            score -= self._q("entity_description_noise_penalty", 0.28)
        if description.startswith(("📚", "📑", "✅", "|")):
            score -= self._q("entity_description_prefix_penalty", 0.12)
        return score

    def _merge_entity_descriptions(self, existing: str, new: str, entity_name: str = "") -> str:
        existing = self._compact_entity_description(existing, entity_name=entity_name)
        new = self._compact_entity_description(new, entity_name=entity_name)
        if not existing:
            return new
        if not new:
            return existing
        if new in existing:
            return existing
        if existing in new:
            return new
        existing_score = self._entity_description_score(existing, entity_name=entity_name)
        new_score = self._entity_description_score(new, entity_name=entity_name)
        if new_score > existing_score:
            return new
        if existing_score > new_score:
            return existing
        return new if len(new) < len(existing) else existing

    def _derive_entity_description(self, entity_name: str, text: str, title: str = "") -> str:
        for sentence in self._split_sentences(text):
            if entity_name in sentence:
                cleaned = self._clean_scan_text(sentence)
                cleaned = re.sub(r"^\d+\.\s*", "", cleaned).lstrip("-*• ")
                if cleaned:
                    return self._compact_entity_description(cleaned, entity_name=entity_name)
        if entity_name.startswith(("http://", "https://")):
            parsed = urlparse(entity_name)
            resource_label = f"{parsed.netloc}{parsed.path}".strip("/")
            if resource_label:
                return f"资源链接：{resource_label[:120]}"
        if title:
            entity_type = ENTITY_TYPE_LABELS.get(self._infer_entity_type(entity_name, title), "实体")
            return self._compact_entity_description(
                f"{title} 中提到的{entity_type}：{entity_name}", entity_name=entity_name
            )
        entity_type = ENTITY_TYPE_LABELS.get(self._infer_entity_type(entity_name, text), "实体")
        return self._compact_entity_description(f"{entity_type}：{entity_name}", entity_name=entity_name)

    def _is_meaningful_name(self, value: str) -> bool:
        if not value:
            return False
        if self._normalize_name(value) in STOP_ENTITY_NAMES:
            return False
        if len(value) < 2:
            return False
        if len(value) > 64:
            return False
        if value.isdigit():
            return False
        if self._looks_like_noise(value):
            return False
        if any(token in value for token in (" 和 ", " 与 ", " 及 ", "、")):
            return False
        if any(value.startswith(prefix) for prefix in ("来", "去", "把", "将", "让", "使", "做", "用")):
            return False
        if any(
            value.startswith(prefix)
            for prefix in (
                "确保",
                "保证",
                "统一",
                "所有",
                "每个",
                "这些",
                "那些",
                "这样",
                "这个",
                "那个",
            )
        ) and not any(hint in self._normalize_name(value) for hint in TECH_ENTITY_HINTS):
            return False
        if any(
            value.endswith(suffix) for suffix in ("提供调用", "来驱动记忆系统", "进行处理", "功能描述", "完整功能描述")
        ):
            return False
        if any(token in value for token in ("提供", "驱动", "调用", "处理", "实现", "开发")) and not value.endswith(
            CHINESE_ENTITY_SUFFIXES
        ):
            return False
        return True

    def _infer_entity_type(self, name: str, context: str = "") -> str:
        lowered = name.casefold()
        full_text = f"{name} {context}".casefold()
        if (
            lowered.startswith(("gpt", "gemma", "llama", "qwen", "deepseek"))
            or re.search(r"\bv\d+(?:\.\d+)?\b", lowered)
            or "模型" in lowered
            or "model" in lowered
        ):
            return "concept"
        if lowered.startswith("http://") or lowered.startswith("https://"):
            return "resource"
        if any(
            token in lowered
            for token in (
                "api key",
                "port",
                "路径",
                "端口",
                "配置",
                "config",
                "/",
                ".yaml",
                ".json",
            )
        ):
            return "configuration"
        if any(
            token in lowered for token in ("公司", "团队", "社区", "组织", "google", "openai", "anthropic", "阿里云")
        ):
            return "organization"
        if any(token in lowered for token in ("项目", "开发", "监控", "集成")):
            return "project"
        if any(token in lowered for token in ("python", "sqlite", "whoosh", "jieba", "framework", "sdk", "库")):
            return "technology"
        if any(token in lowered for token in ("openclaw", "ollama", "openrouter", "router", "工具", "服务")) or (
            "系统" in lowered and len(lowered) <= 16
        ):
            return "tool"
        if any(token in lowered for token in ("事件", "发布", "误报", "事故")):
            return "event"
        if any(token in lowered for token in ("原则", "决策", "策略", "决定")):
            return "decision"
        if any(token in lowered for token in ("模型", "架构", "图谱", "检索", "知识", "曲线")):
            return "concept"
        if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}$", name):
            return "person"
        if any(token in full_text for token in ("项目", "开发", "监控", "集成")):
            return "project"
        if any(token in full_text for token in ("系统", "工具", "服务")):
            return "tool"
        return "concept"

    def _select_best_entity_candidate(self, chunk: str, prefer: str = "first") -> list[str]:
        candidates = self._candidate_entities_from_text(chunk)
        if not candidates:
            fallback: list[str] = []
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_.:+/-]{1,}|[\u4e00-\u9fff]{2,16}", chunk or ""):
                token = self._normalize_candidate(token)
                if not self._is_meaningful_name(token):
                    continue
                min_q = self._q("relation_extract_entity_min_quality_relaxed", 0.35)
                if self._entity_quality_score(token) < min_q:
                    continue
                fallback.append(token)
            seen = set()
            dedup = []
            for x in fallback:
                k = self._normalize_name(x)
                if k in seen:
                    continue
                seen.add(k)
                dedup.append(x)
            candidates = dedup
        if prefer == "longest":
            candidates.sort(key=len, reverse=True)
        return candidates

    def _compute_memory_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _model_like_name(self, value: str) -> bool:
        lowered = value.casefold()
        return bool(
            lowered.startswith(("gpt", "qwen", "llama", "gemma", "deepseek", "minimax"))
            or re.search(r"\b(?:r\d+|v\d+(?:\.\d+)?)\b", lowered)
            or "模型" in lowered
        )

    def _fetch_entity_by_id(self, entity_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, canonical_name, name_key, entity_type, description,
                       importance, access_count, created_at, updated_at, source_memory_ref
                FROM entities
                WHERE id = ?
                """,
                (entity_id,),
            ).fetchone()
            return dict(row) if row else None

    def _resolve_entity_id(self, name: str) -> int | None:
        normalized = self._normalize_name(name)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM entities WHERE name_key = ?",
                (normalized,),
            ).fetchone()
            if row:
                return int(row["id"])
            row = conn.execute(
                "SELECT entity_id FROM entity_aliases WHERE alias_key = ?",
                (normalized,),
            ).fetchone()
            if row:
                return int(row["entity_id"])
        return None

    def _fuzzy_match_entity_id(self, name: str) -> int | None:
        normalized = self._normalize_name(name)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, canonical_name, name_key FROM entities ORDER BY importance DESC, access_count DESC"
            ).fetchall()
        best_id = None
        best_score = 0.0
        for row in rows:
            score = SequenceMatcher(None, normalized, row["name_key"]).ratio()
            if score > best_score:
                best_score = score
                best_id = int(row["id"])
        if best_score >= self._q("entity_alias_fuzzy_match_min_score", 0.9):
            return best_id
        return None

    def get_entity(self, name: str) -> dict[str, Any] | None:
        entity_id = self._resolve_entity_id(name)
        if entity_id is None:
            return None
        entity = self._fetch_entity_by_id(entity_id)
        if not entity:
            return None
        entity["aliases"] = self._list_aliases(entity_id)
        return entity

    def _list_aliases(self, entity_id: int) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id = ? ORDER BY alias COLLATE NOCASE",
                (entity_id,),
            ).fetchall()
        return [row["alias"] for row in rows]

    def add_entity(
        self,
        name: str,
        entity_type: str = "concept",
        aliases: Sequence[str] | None = None,
        description: str = "",
        importance: float = 0.5,
        source_memory_ref: str | None = None,
    ) -> dict[str, Any] | None:
        canonical_name = self._normalize_candidate(name)
        if not self._is_meaningful_name(canonical_name):
            return None
        entity_type = (
            entity_type if entity_type in ENTITY_TYPES else self._infer_entity_type(canonical_name, description)
        )
        importance = max(0.0, min(1.0, float(importance)))
        description = (description or "").strip()
        now = self._utcnow()
        entity_id = self._resolve_entity_id(canonical_name) or self._fuzzy_match_entity_id(canonical_name)

        with self._connect() as conn:
            cursor = conn.cursor()
            if entity_id is None:
                try:
                    cursor.execute(
                        """
                        INSERT INTO entities (
                            canonical_name, name_key, entity_type, description, importance,
                            access_count, created_at, updated_at, source_memory_ref
                        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                        """,
                        (
                            canonical_name,
                            self._normalize_name(canonical_name),
                            entity_type,
                            description,
                            importance,
                            now,
                            now,
                            source_memory_ref,
                        ),
                    )
                    last_id = cursor.lastrowid
                    entity_id = int(last_id) if isinstance(last_id, int) else None
                    if entity_id is None:
                        raise RuntimeError("entity insert did not return lastrowid")
                except sqlite3.IntegrityError:
                    row = cursor.execute(
                        "SELECT id FROM entities WHERE name_key = ?",
                        (self._normalize_name(canonical_name),),
                    ).fetchone()
                    entity_id = int(row["id"]) if row else None
                    if entity_id is None:
                        raise
            else:
                existing = cursor.execute(
                    "SELECT description, importance, access_count FROM entities WHERE id = ?",
                    (entity_id,),
                ).fetchone()
                merged_description = self._merge_entity_descriptions(
                    existing["description"] or "", description, entity_name=canonical_name
                )
                new_importance = max(float(existing["importance"]), importance)
                cursor.execute(
                    """
                    UPDATE entities
                    SET entity_type = ?, description = ?, importance = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (entity_type, merged_description, new_importance, now, entity_id),
                )

            for alias in aliases or []:
                alias = self._normalize_candidate(alias)
                if not self._is_meaningful_name(alias):
                    continue
                alias_key = self._normalize_name(alias)
                if alias_key == self._normalize_name(canonical_name):
                    continue
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO entity_aliases (entity_id, alias, alias_key, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (entity_id, alias, alias_key, now),
                )
            conn.commit()

        return self.get_entity(canonical_name)

    def update_entity(self, name: str, **changes: Any) -> dict[str, Any] | None:
        entity = self.get_entity(name)
        if not entity:
            return None
        description = changes.get("description", entity.get("description", ""))
        importance = max(0.0, min(1.0, float(changes.get("importance", entity.get("importance", 0.5)))))
        aliases = list(entity.get("aliases", [])) + list(changes.get("aliases", []) or [])
        entity_type = changes.get("entity_type", entity.get("entity_type", "concept"))
        return self.add_entity(
            entity["canonical_name"],
            entity_type=entity_type,
            aliases=aliases,
            description=description,
            importance=importance,
            source_memory_ref=changes.get("source_memory_ref", entity.get("source_memory_ref")),
        )

    def delete_entity(self, name: str) -> dict[str, Any]:
        entity = self.get_entity(name)
        if not entity:
            return {"status": "not_found", "entity": name}
        with self._connect() as conn:
            cursor = conn.cursor()
            relation_count = cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM relations
                WHERE subject_entity_id = ? OR object_entity_id = ?
                """,
                (entity["id"], entity["id"]),
            ).fetchone()["total"]
            cursor.execute("DELETE FROM memory_anchors WHERE entity_id = ?", (entity["id"],))
            cursor.execute(
                "DELETE FROM relations WHERE subject_entity_id = ? OR object_entity_id = ?",
                (entity["id"], entity["id"]),
            )
            cursor.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (entity["id"],))
            cursor.execute("DELETE FROM entities WHERE id = ?", (entity["id"],))
            conn.commit()
        return {
            "status": "success",
            "deleted_entity": entity["canonical_name"],
            "deleted_relations": relation_count,
            "warning": relation_count >= 5 or entity["importance"] >= self._q("entity_delete_warning_importance", 0.8),
        }

    def search_entities(
        self,
        query: str,
        entity_type: str | None = None,
        fuzzy: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = limit or int(self.settings["default_return_limit"])
        query_key = self._normalize_name(query)
        like = f"%{query.strip()}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT e.id, e.canonical_name, e.entity_type, e.description,
                       e.importance, e.access_count, e.created_at, e.updated_at,
                       e.source_memory_ref
                FROM entities e
                LEFT JOIN entity_aliases a ON a.entity_id = e.id
                WHERE (
                    e.canonical_name LIKE ?
                    OR a.alias LIKE ?
                    OR e.name_key = ?
                )
                """
                + (" AND e.entity_type = ?" if entity_type else "")
                + """
                ORDER BY e.importance DESC, e.updated_at DESC
                LIMIT ?
                """,
                tuple(
                    value
                    for value in (
                        like,
                        like,
                        query_key,
                        entity_type,
                        limit,
                    )
                    if value is not None
                ),
            ).fetchall()
        candidates = [dict(row) for row in rows]
        if fuzzy:
            existing_ids = {row["id"] for row in candidates}
            with self._connect() as conn:
                all_rows = conn.execute(
                    "SELECT id, canonical_name, entity_type, description, importance, "
                    "access_count, created_at, updated_at, source_memory_ref "
                    "FROM entities"
                ).fetchall()
            fuzzy_rows: list[tuple[float, dict[str, Any]]] = []
            for row in all_rows:
                if row["id"] in existing_ids:
                    continue
                if entity_type and row["entity_type"] != entity_type:
                    continue
                score = SequenceMatcher(None, query_key, self._normalize_name(row["canonical_name"])).ratio()
                if score >= self._q("entity_search_fuzzy_min_score", 0.62):
                    fuzzy_rows.append((score, dict(row)))
            fuzzy_rows.sort(key=lambda item: (item[0], item[1]["importance"]), reverse=True)
            for _, row in fuzzy_rows:
                candidates.append(row)
                if len(candidates) >= limit:
                    break
        for row in candidates:
            row["aliases"] = self._list_aliases(int(row["id"]))
        return candidates[:limit]

    def list_entities(
        self,
        entity_type: str | None = None,
        sort_by: str = "importance",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = limit or int(self.settings["default_return_limit"])
        order_by = "importance DESC, updated_at DESC" if sort_by == "importance" else "updated_at DESC, importance DESC"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, canonical_name, entity_type, description, importance,
                       access_count, created_at, updated_at, source_memory_ref
                FROM entities
                {"WHERE entity_type = ?" if entity_type else ""}
                ORDER BY {order_by}
                LIMIT ?
                """,
                tuple(value for value in (entity_type, limit) if value is not None),
            ).fetchall()
        results = [dict(row) for row in rows]
        for row in results:
            row["aliases"] = self._list_aliases(int(row["id"]))
        return results

    def touch_entity_access(self, entity_id: int) -> None:
        increment = float(self.settings["access_increment"])
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE entities
                    SET access_count = access_count + 1,
                        importance = MIN(1.0, importance + ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (increment, self._utcnow(), entity_id),
                )
                conn.commit()
        except sqlite3.OperationalError:
            # Context reads should still work in readonly or sandboxed environments.
            return

    def add_relation(
        self,
        subject_name: str,
        relation_type: str,
        object_name: str,
        strength: float = 0.5,
        description: str = "",
        source_memory_ref: str | None = None,
        confidence: float = 0.6,
        subject_type: str | None = None,
        object_type: str | None = None,
    ) -> dict[str, Any] | None:
        relation_type = relation_type if relation_type in RELATION_TYPES else "related_to"
        relation_context = (
            description or f"{subject_name} {RELATION_LABELS.get(relation_type, relation_type)} {object_name}"
        )
        subject = self.add_entity(
            subject_name,
            subject_type or self._infer_entity_type(subject_name, relation_context),
            description=self._derive_entity_description(subject_name, relation_context),
            source_memory_ref=source_memory_ref,
        )
        object_entity = self.add_entity(
            object_name,
            object_type or self._infer_entity_type(object_name, relation_context),
            description=self._derive_entity_description(object_name, relation_context),
            source_memory_ref=source_memory_ref,
        )
        if not subject or not object_entity:
            return None
        if subject["id"] == object_entity["id"]:
            return None
        now = self._utcnow()
        strength = max(float(self.settings["min_relation_strength"]), min(1.0, float(strength)))
        confidence = max(0.0, min(1.0, float(confidence)))
        increment = float(self.settings["duplicate_merge_increment"])
        with self._connect() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                """
                SELECT id, strength, description, confidence
                FROM relations
                WHERE subject_entity_id = ? AND object_entity_id = ? AND relation_type = ?
                """,
                (subject["id"], object_entity["id"], relation_type),
            ).fetchone()
            if row:
                prev_strength = float(row["strength"])
                prev_conf = float(row["confidence"])
                merged_strength = min(1.0, max(float(row["strength"]), strength) + increment)
                merged_confidence = min(1.0, max(float(row["confidence"]), confidence))
                merged_description = row["description"] or ""
                if description and description not in merged_description:
                    merged_description = (
                        description.strip()
                        if not merged_description
                        else f"{merged_description} {description.strip()}".strip()
                    )
                desc_changed = merged_description != (row["description"] or "")
                cursor.execute(
                    """
                    UPDATE relations
                    SET strength = ?, description = ?, confidence = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (merged_strength, merged_description, merged_confidence, now, row["id"]),
                )
                relation_id = int(row["id"])
                strength_delta = max(0.0, merged_strength - prev_strength)
                confidence_delta = max(0.0, merged_confidence - prev_conf)
                if strength_delta > 0 or confidence_delta > 0:
                    writeback_mode = "reinforced"
                elif desc_changed:
                    writeback_mode = "merged"
                else:
                    writeback_mode = "unchanged"
                updated_relations_count = 1 if writeback_mode in {"reinforced", "merged"} else 0
            else:
                try:
                    cursor.execute(
                        """
                        INSERT INTO relations (
                            subject_entity_id, object_entity_id, relation_type,
                            strength, description, confidence, access_count,
                            created_at, updated_at, source_memory_ref
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                        """,
                        (
                            subject["id"],
                            object_entity["id"],
                            relation_type,
                            strength,
                            description.strip(),
                            confidence,
                            now,
                            now,
                            source_memory_ref,
                        ),
                    )
                    last_rel_id = cursor.lastrowid
                    relation_id = int(last_rel_id) if isinstance(last_rel_id, int) else 0
                    if relation_id <= 0:
                        raise RuntimeError("relation insert did not return lastrowid")
                    strength_delta = max(0.0, strength)
                    confidence_delta = max(0.0, confidence)
                    writeback_mode = "inserted"
                    updated_relations_count = 1
                except sqlite3.IntegrityError:
                    # Concurrent ingest may insert the same relation first.
                    # Treat it as an existing relation merge path instead of surfacing thread warnings.
                    conn.rollback()
                    existing = cursor.execute(
                        """
                        SELECT id, strength, description, confidence
                        FROM relations
                        WHERE subject_entity_id = ? AND object_entity_id = ? AND relation_type = ?
                        """,
                        (subject["id"], object_entity["id"], relation_type),
                    ).fetchone()
                    if existing is None:
                        raise
                    relation_id = int(existing["id"])
                    strength_delta = 0.0
                    confidence_delta = 0.0
                    writeback_mode = "concurrent_existing"
                    updated_relations_count = 0
            conn.commit()
        relation = self.get_relation(relation_id)
        if relation is None:
            return None
        relation["writeback_mode"] = writeback_mode
        relation["strength_delta_sum"] = round(float(strength_delta), 6)
        relation["confidence_delta_sum"] = round(float(confidence_delta), 6)
        relation["updated_relations_count"] = int(updated_relations_count)
        return relation

    def get_relation(self, relation_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT r.id, r.relation_type, r.strength, r.description, r.confidence,
                       r.access_count, r.created_at, r.updated_at, r.source_memory_ref,
                       s.canonical_name AS subject_name, s.entity_type AS subject_type,
                       o.canonical_name AS object_name, o.entity_type AS object_type
                FROM relations r
                JOIN entities s ON s.id = r.subject_entity_id
                JOIN entities o ON o.id = r.object_entity_id
                WHERE r.id = ?
                """,
                (relation_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_relations(
        self,
        entity_name: str | None = None,
        relation_type: str | None = None,
        direction: str = "both",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = limit or int(self.settings["default_return_limit"])
        clauses = []
        params: list[Any] = []
        if entity_name:
            entity_id = self._resolve_entity_id(entity_name)
            if entity_id is None:
                return []
            if direction == "outgoing":
                clauses.append("r.subject_entity_id = ?")
                params.append(entity_id)
            elif direction == "incoming":
                clauses.append("r.object_entity_id = ?")
                params.append(entity_id)
            else:
                clauses.append("(r.subject_entity_id = ? OR r.object_entity_id = ?)")
                params.extend([entity_id, entity_id])
        if relation_type:
            clauses.append("r.relation_type = ?")
            params.append(relation_type)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT r.id, r.relation_type, r.strength, r.description, r.confidence,
                       r.access_count, r.created_at, r.updated_at, r.source_memory_ref,
                       s.canonical_name AS subject_name, o.canonical_name AS object_name
                FROM relations r
                JOIN entities s ON s.id = r.subject_entity_id
                JOIN entities o ON o.id = r.object_entity_id
                {where_clause}
                ORDER BY r.strength DESC, r.updated_at DESC
                LIMIT ?
                """,
                tuple(params + [limit]),
            ).fetchall()
        state = self._load_quality_state()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            status = self._relation_quality_status(item, state=state)
            item.update(status)
            out.append(item)
        return out

    def relation_count(self, entity_name: str) -> int:
        entity_id = self._resolve_entity_id(entity_name)
        if entity_id is None:
            return 0
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as total
                FROM relations
                WHERE subject_entity_id = ? OR object_entity_id = ?
                """,
                (entity_id, entity_id),
            ).fetchone()
        return int(row["total"]) if row else 0

    def gap_report(
        self,
        min_importance: float = 0.6,
        max_relations: int = 1,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.canonical_name, e.entity_type, e.importance,
                       (
                         SELECT COUNT(*)
                         FROM relations r
                         WHERE r.subject_entity_id = e.id OR r.object_entity_id = e.id
                       ) as relation_count
                FROM entities e
                WHERE e.importance >= ?
                ORDER BY relation_count ASC, e.importance DESC
                LIMIT ?
                """,
                (float(min_importance), int(limit)),
            ).fetchall()
        results = [dict(row) for row in rows if int(row["relation_count"]) <= int(max_relations)]
        return results

    def relation_between(self, subject_name: str, object_name: str) -> list[dict[str, Any]]:
        subject_id = self._resolve_entity_id(subject_name)
        object_id = self._resolve_entity_id(object_name)
        if subject_id is None or object_id is None:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.relation_type, r.strength, r.description, r.confidence,
                       r.created_at, r.updated_at,
                       s.canonical_name AS subject_name, o.canonical_name AS object_name
                FROM relations r
                JOIN entities s ON s.id = r.subject_entity_id
                JOIN entities o ON o.id = r.object_entity_id
                WHERE (r.subject_entity_id = ? AND r.object_entity_id = ?)
                   OR (r.subject_entity_id = ? AND r.object_entity_id = ?)
                ORDER BY r.strength DESC, r.updated_at DESC
                """,
                (subject_id, object_id, object_id, subject_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def _neighbor_rows(self, entity_id: int, relation_type: str | None = None) -> list[sqlite3.Row]:
        clauses = ["(r.subject_entity_id = ? OR r.object_entity_id = ?)"]
        params: list[Any] = [entity_id, entity_id]
        if relation_type:
            clauses.append("r.relation_type = ?")
            params.append(relation_type)
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT r.id, r.relation_type, r.strength,
                       r.subject_entity_id, r.object_entity_id,
                       s.canonical_name AS subject_name,
                       o.canonical_name AS object_name
                FROM relations r
                JOIN entities s ON s.id = r.subject_entity_id
                JOIN entities o ON o.id = r.object_entity_id
                WHERE {" AND ".join(clauses)}
                ORDER BY r.strength DESC, r.updated_at DESC
                """,
                tuple(params),
            ).fetchall()

    def get_neighbors(
        self,
        entity_name: str,
        depth: int | None = None,
        relation_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        depth = depth or int(self.settings["default_query_depth"])
        depth = max(1, min(int(self.settings["max_query_depth"]), depth))
        limit = limit or int(self.settings["default_return_limit"])
        start_id = self._resolve_entity_id(entity_name)
        if start_id is None:
            return []
        visited = {start_id}
        frontier = [(start_id, 0, 1.0)]
        results: list[dict[str, Any]] = []
        quality_state = self._load_quality_state()
        while frontier and len(results) < limit:
            current_id, current_depth, path_strength = frontier.pop(0)
            if current_depth >= depth:
                continue
            for row in self._neighbor_rows(current_id, relation_type):
                q = self._relation_quality_status(dict(row), state=quality_state)
                if bool(q.get("soft_isolated", False)):
                    continue
                next_id = int(
                    row["object_entity_id"] if row["subject_entity_id"] == current_id else row["subject_entity_id"]
                )
                if next_id in visited:
                    continue
                visited.add(next_id)
                effective_strength = float(row["strength"]) * path_strength
                entity = self._fetch_entity_by_id(next_id)
                if not entity:
                    continue
                results.append(
                    {
                        "entity": entity,
                        "degree": current_depth + 1,
                        "via_relation": RELATION_LABELS.get(row["relation_type"], row["relation_type"]),
                        "relation_type": row["relation_type"],
                        "strength": float(row["strength"]),
                        "effective_strength": effective_strength,
                        "relation_status": q.get("relation_status", "stable"),
                    }
                )
                frontier.append((next_id, current_depth + 1, effective_strength))
                if len(results) >= limit:
                    break
        results.sort(
            key=lambda item: (item["effective_strength"], item["entity"]["importance"]),
            reverse=True,
        )
        return results[:limit]

    def shortest_path(self, start_name: str, end_name: str, max_depth: int = 3) -> dict[str, Any]:
        max_depth = max(1, min(int(self.settings["max_query_depth"]), max_depth))
        start_id = self._resolve_entity_id(start_name)
        end_id = self._resolve_entity_id(end_name)
        if start_id is None or end_id is None:
            return {"status": "not_found", "path": []}
        if start_id == end_id:
            entity = self._fetch_entity_by_id(start_id)
            return {"status": "success", "path": [entity["canonical_name"]] if entity else []}

        queue: list[tuple[int, list[dict[str, Any]]]] = [(start_id, [])]
        visited = {start_id}
        while queue:
            current_id, path = queue.pop(0)
            if len(path) >= max_depth:
                continue
            for row in self._neighbor_rows(current_id):
                next_id = int(
                    row["object_entity_id"] if row["subject_entity_id"] == current_id else row["subject_entity_id"]
                )
                if next_id in visited:
                    continue
                step = {
                    "from": row["subject_name"] if row["subject_entity_id"] == current_id else row["object_name"],
                    "relation": RELATION_LABELS.get(row["relation_type"], row["relation_type"]),
                    "to": row["object_name"] if row["subject_entity_id"] == current_id else row["subject_name"],
                    "strength": float(row["strength"]),
                }
                new_path = path + [step]
                if next_id == end_id:
                    return {"status": "success", "path": new_path}
                visited.add(next_id)
                queue.append((next_id, new_path))
        return {"status": "no_path", "path": []}

    def related_entities(self, entity_name: str, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or int(self.settings["default_return_limit"])
        entity = self.get_entity(entity_name)
        if not entity:
            return []
        neighbors = self.get_neighbors(entity_name, depth=2, limit=limit * 2)
        now = datetime.now(timezone.utc)
        results: list[dict[str, Any]] = []
        for neighbor in neighbors:
            updated_at = self._parse_utc_datetime(neighbor["entity"]["updated_at"])
            if updated_at is None:
                updated_at = now
            age_days = max(1.0, (now - updated_at).total_seconds() / 86400)
            freshness = 1.0 / min(5.0, 1.0 + age_days / 7.0)
            relatedness = neighbor["effective_strength"] * float(neighbor["entity"]["importance"]) * freshness
            results.append(
                {
                    "entity": neighbor["entity"],
                    "relation_type": neighbor["relation_type"],
                    "via_relation": neighbor["via_relation"],
                    "relatedness": relatedness,
                    "freshness": freshness,
                }
            )
        results.sort(key=lambda item: item["relatedness"], reverse=True)
        return results[:limit]

    def _upsert_anchor(
        self,
        memory_ref: str,
        source: str,
        title: str,
        memory_hash: str,
        snippet: str,
        entity_id: int | None = None,
        relation_id: int | None = None,
    ) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                """
                SELECT id FROM memory_anchors
                WHERE memory_ref = ?
                  AND COALESCE(entity_id, -1) = COALESCE(?, -1)
                  AND COALESCE(relation_id, -1) = COALESCE(?, -1)
                """,
                (memory_ref, entity_id, relation_id),
            ).fetchone()
            if row:
                cursor.execute(
                    """
                    UPDATE memory_anchors
                    SET source = ?, title = ?, memory_hash = ?, snippet = ?
                    WHERE id = ?
                    """,
                    (source, title, memory_hash, snippet, row["id"]),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO memory_anchors (
                        memory_ref, source, title, memory_hash, entity_id, relation_id, snippet, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_ref,
                        source,
                        title,
                        memory_hash,
                        entity_id,
                        relation_id,
                        snippet,
                        self._utcnow(),
                    ),
                )
            conn.commit()

    def _mark_memory_extracted(
        self, memory_ref: str, source: str, title: str, memory_hash: str, extractor_mode: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_extractions (memory_ref, source, title, memory_hash, extractor_mode, extracted_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_ref)
                DO UPDATE SET
                    source = excluded.source,
                    title = excluded.title,
                    memory_hash = excluded.memory_hash,
                    extractor_mode = excluded.extractor_mode,
                    extracted_at = excluded.extracted_at
                """,
                (memory_ref, source, title, memory_hash, extractor_mode, self._utcnow()),
            )
            conn.commit()

    def _needs_extraction(self, memory_ref: str, memory_hash: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT memory_hash FROM memory_extractions WHERE memory_ref = ?",
                (memory_ref,),
            ).fetchone()
        return row is None or row["memory_hash"] != memory_hash

    def _candidate_entities_from_text(self, text: str) -> list[str]:
        text = self._clean_scan_text(text)
        candidates: list[str] = []
        quoted = re.findall(r"[\"“”'‘’`]{1}([^\"“”'‘’`]{2,64})[\"“”'‘’`]{1}", text)
        candidates.extend(quoted)
        candidates.extend(re.findall(r"https?://[^\s]+", text))
        candidates.extend(
            re.findall(
                (
                    r"\b(?:[A-Z][A-Za-z0-9.+:_/-]{1,}"
                    r"(?:\s+[A-Z0-9][A-Za-z0-9.+:_/-]{1,}){0,3}"
                    r"|[a-z][a-z0-9.+:_/-]*[0-9:+._-][a-z0-9.+:_/-]*)\b"
                ),
                text,
            )
        )
        suffix_pattern = r"[\u4e00-\u9fffA-Za-z0-9.+:_/-]{2,24}(?:" + "|".join(CHINESE_ENTITY_SUFFIXES) + r")"
        candidates.extend(re.findall(suffix_pattern, text))
        cleaned: list[str] = []
        seen = set()
        for candidate in candidates:
            candidate = self._normalize_candidate(candidate)
            if not self._is_meaningful_name(candidate):
                continue
            if self._entity_quality_score(candidate) < self._q("entity_candidate_min_quality", 0.45):
                continue
            key = self._normalize_name(candidate)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(candidate)
        return cleaned

    def _rule_extract_entities(self, text: str) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        for sentence in self._split_sentences(text):
            for candidate in self._candidate_entities_from_text(sentence):
                counts[candidate] += 1
        if not counts:
            for candidate in self._candidate_entities_from_text(text):
                counts[candidate] += 1
        for candidate, count in counts.items():
            quality = self._entity_quality_score(candidate)
            if quality < self._q("entity_weak_quality_threshold", 0.52) and count < 2:
                continue
            imp_min = self._q("entity_importance_min_value", 0.45)
            imp_max = self._q("entity_importance_max_value", 0.90)
            imp_base = self._q("entity_importance_base_value", 0.35)
            imp_quality_factor = self._q("entity_importance_quality_factor", 0.35)
            imp_count_cap = self._q("entity_importance_count_cap", 0.20)
            imp_count_factor = self._q("entity_importance_count_factor", 0.07)
            entities.append(
                {
                    "name": candidate,
                    "type": self._infer_entity_type(candidate, text),
                    "aliases": [],
                    "description": self._derive_entity_description(candidate, text),
                    "importance": max(
                        imp_min,
                        min(
                            imp_max,
                            imp_base + quality * imp_quality_factor + min(imp_count_cap, count * imp_count_factor),
                        ),
                    ),
                    "frequency": count,
                    "quality": quality,
                }
            )
        return entities

    def _extract_relation_candidates(self, sentence: str) -> list[dict[str, Any]]:
        sentence = sentence.strip()
        if len(sentence) < int(self._q("relation_extract_min_sentence_len", 4.0)):
            return []
        patterns = [
            (r"(?P<a>.+?)\s*(?:依赖|需要)\s*(?P<b>.+)", "depends_on", 0.78),
            (r"(?P<a>.+?)\s*(?:是|属于)\s*(?P<b>.+?)的一部分", "part_of", 0.72),
            (
                r"(?P<a>.+?)\s*(?:属于|归属|隶属于)\s*(?P<b>.+?)(?:模块|子系统|体系|项目)",
                "part_of",
                0.74,
            ),
            (r"(?P<a>.+?)\s*(?:由|由\s*.+\s*所)?(?:组成|构成)\s*(?P<b>.+)", "part_of", 0.70),
            (r"(?P<a>.+?)\s*(?:使用|调用|接入|采用)(?:了)?\s*(?P<b>.+)", "uses", 0.76),
            (r"(?P<a>.+?)\s*(?:通过|借助)\s*(?P<b>.+?)\s*(?:调用|访问|连接|运行)", "uses", 0.74),
            (r"(?P<a>.+?)\s*(?:基于|构建于|建立在)\s*(?P<b>.+)", "depends_on", 0.77),
            (r"(?P<a>.+?)\s*(?:支持|兼容|适配)\s*(?P<b>.+)", "uses", 0.71),
            (r"(?P<a>.+?)\s*(?:集成|整合)\s*(?P<b>.+)", "uses", 0.74),
            (r"(?P<a>.+?)\s*(?:驱动|由.+驱动)\s*(?P<b>.+)", "depends_on", 0.73),
            (r"(?P<a>.+?)\s*(?:类似于|类似|接近)\s*(?P<b>.+)", "similar_to", 0.68),
            (r"(?P<a>.+?)\s*(?:替代|取代)\s*(?P<b>.+)", "replaces", 0.7),
            (r"(?P<a>.+?)\s*(?:创建了|开发了|实现了)\s*(?P<b>.+)", "creates", 0.75),
            (r"(?P<a>.+?)\s*(?:生成|产出|构建|搭建)\s*(?P<b>.+)", "creates", 0.73),
            (r"(?P<a>.+?)\s*(?:归属于|属于)\s*(?P<b>.+)", "belongs_to", 0.74),
            (r"(?P<a>.+?)\s*(?:导致|引发)\s*(?P<b>.+)", "causes", 0.74),
            (r"(?P<a>.+?)\s*(?:矛盾于|冲突于)\s*(?P<b>.+)", "contradicts", 0.7),
            (r"(?P<a>.+?)\s*(?:演变自|发展自)\s*(?P<b>.+)", "evolves_from", 0.72),
            (
                r"(?P<a>.+?)\s*(?:由|从)\s*(?P<b>.+?)\s*(?:演进|升级|改进|迭代)(?:而来)?",
                "evolves_from",
                0.71,
            ),
            (r"从(?P<a>.+?)中(?:学到了|得到)\s*(?P<b>.+)", "learns_from", 0.75),
            (r"(?P<a>.+?)\s+depends on\s+(?P<b>.+)", "depends_on", 0.78),
            (r"(?P<a>.+?)\s+uses\s+(?P<b>.+)", "uses", 0.76),
            (r"(?P<a>.+?)\s+is part of\s+(?P<b>.+)", "part_of", 0.72),
            (r"(?P<a>.+?)\s+is(?:\s+an?)?\s+module of\s+(?P<b>.+)", "part_of", 0.73),
            (r"(?P<a>.+?)\s+belongs to\s+(?P<b>.+)", "belongs_to", 0.74),
            (r"(?P<a>.+?)\s+is similar to\s+(?P<b>.+)", "similar_to", 0.68),
            (r"(?P<a>.+?)\s+replaces\s+(?P<b>.+)", "replaces", 0.7),
            (r"(?P<a>.+?)\s+creates\s+(?P<b>.+)", "creates", 0.75),
            (r"(?P<a>.+?)\s+evolves from\s+(?P<b>.+)", "evolves_from", 0.72),
        ]
        extracted: list[dict[str, Any]] = []
        for pattern, relation_type, confidence in patterns:
            match = re.search(pattern, sentence)
            if not match:
                continue
            confidence = self._q(f"relation_extract_confidence_{relation_type}", confidence)
            subject_candidates = self._select_best_entity_candidate(match.group("a"), prefer="first")
            object_candidates = self._select_best_entity_candidate(match.group("b"), prefer="first")
            if not subject_candidates or not object_candidates:
                continue
            subject = subject_candidates[0]
            min_entity_quality = self._q("relation_extract_entity_min_quality", 0.55)
            if relation_type in {"part_of", "creates", "evolves_from"}:
                min_entity_quality = self._q("relation_extract_entity_min_quality_relaxed", 0.35)
            if self._entity_quality_score(subject) < min_entity_quality:
                continue
            object_limit = int(self._q("relation_extract_max_object_candidates", 3.0))
            object_items = [
                item
                for item in object_candidates[:object_limit]
                if self._normalize_name(item) != self._normalize_name(subject)
            ]
            for object_name in object_items:
                if self._entity_quality_score(object_name) < min_entity_quality:
                    continue
                extracted.append(
                    {
                        "subject": subject,
                        "type": relation_type,
                        "object": object_name,
                        "strength": confidence,
                        "description": sentence,
                        "confidence": confidence,
                    }
                )
        if extracted:
            return extracted

        entities = self._candidate_entities_from_text(sentence)
        pair_conf = self._q("relation_extract_pair_fallback_confidence", 0.2)
        pair_max_sentence_len = int(self._q("relation_extract_pair_max_sentence_len", 60.0))
        if (
            len(entities) == 2
            and all(
                self._entity_quality_score(entity) >= self._q("relation_extract_high_pair_quality", 0.86)
                for entity in entities
            )
            and len(sentence) <= pair_max_sentence_len
        ):
            return [
                {
                    "subject": entities[0],
                    "type": "related_to",
                    "object": entities[1],
                    "strength": pair_conf,
                    "description": sentence,
                    "confidence": pair_conf,
                }
            ]
        return []

    def _rule_extract_relations(self, text: str) -> list[dict[str, Any]]:
        sentences = [part.strip() for part in re.split(r"[。！？!?;\n]+", text) if part.strip()]
        relations: list[dict[str, Any]] = []
        seen = set()
        for sentence in sentences:
            for relation in self._extract_relation_candidates(sentence):
                key = (
                    self._normalize_name(relation["subject"]),
                    relation["type"],
                    self._normalize_name(relation["object"]),
                )
                if key in seen:
                    continue
                seen.add(key)
                relations.append(relation)
        return relations

    def _infer_context_relations(
        self,
        entities: Sequence[dict[str, Any]],
        existing_relations: Sequence[dict[str, Any]],
        text: str,
    ) -> list[dict[str, Any]]:
        if len(entities) < 2:
            return []
        existing_keys = {
            (
                self._normalize_name(item["subject"]),
                item["type"],
                self._normalize_name(item["object"]),
            )
            for item in existing_relations
        }
        prepared_sentences = self._split_sentences(text)
        ranked_entities = sorted(
            entities,
            key=lambda item: (
                float(item.get("quality", self._entity_quality_score(item["name"]))),
                float(item.get("importance", 0.5)),
                int(item.get("frequency", 1)),
            ),
            reverse=True,
        )
        anchor_limit = int(self._q("infer_anchor_max_entities", 4.0))
        anchors = ranked_entities[: min(anchor_limit, len(ranked_entities))]
        inferred: list[dict[str, Any]] = []

        for anchor in anchors:
            anchor_name = anchor["name"]
            anchor_type = anchor.get("type", "concept")
            for entity in ranked_entities:
                if entity["name"] == anchor_name:
                    continue
                entity_name = entity["name"]
                entity_type = entity.get("type", "concept")
                key_prefix = self._normalize_name(anchor_name)
                key_suffix = self._normalize_name(entity_name)
                if any(
                    existing_key[:1] == (key_prefix,) and existing_key[2] == key_suffix
                    for existing_key in existing_keys
                ):
                    continue

                sentence_match = next(
                    (
                        sentence
                        for sentence in prepared_sentences
                        if anchor_name in sentence and entity_name in sentence
                    ),
                    "",
                )
                min_freq_without_sentence = int(self._q("infer_min_frequency_without_sentence", 2.0))
                if not sentence_match and int(entity.get("frequency", 1)) < min_freq_without_sentence:
                    continue

                relation_type = None
                confidence = self._q("infer_base_confidence", 0.42)
                lowered_sentence = sentence_match.casefold()
                if anchor_type in {"tool", "project"} and entity_type in {
                    "technology",
                    "configuration",
                }:
                    relation_type = "depends_on"
                    confidence = self._q("infer_depends_on_confidence", 0.58)
                elif anchor_type in {"tool", "project"} and entity_type in {"concept", "resource"}:
                    relation_type = "uses"
                    confidence = self._q("infer_uses_confidence", 0.54)
                elif anchor_type == "organization" and self._model_like_name(entity_name):
                    relation_type = "belongs_to"
                    confidence = self._q("infer_belongs_to_confidence", 0.57)
                elif entity_type == "organization" and self._model_like_name(anchor_name):
                    relation_type = "belongs_to"
                    confidence = self._q("infer_belongs_to_confidence", 0.57)
                elif ("便宜" in lowered_sentence or "价格" in lowered_sentence or "零成本" in lowered_sentence) and (
                    self._model_like_name(anchor_name) or self._model_like_name(entity_name)
                ):
                    relation_type = "related_to"
                    confidence = self._q("infer_related_price_confidence", 0.5)
                elif sentence_match and (
                    anchor_type in {"tool", "technology", "organization", "project"}
                    or entity_type in {"tool", "technology", "organization", "project"}
                    or self._model_like_name(anchor_name)
                    or self._model_like_name(entity_name)
                ):
                    relation_type = "related_to"
                    confidence = self._q("infer_related_generic_confidence", 0.4)

                if not relation_type:
                    continue
                key = (key_prefix, relation_type, key_suffix)
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                inferred.append(
                    {
                        "subject": anchor_name,
                        "type": relation_type,
                        "object": entity_name,
                        "strength": confidence,
                        "description": sentence_match
                        or f"{anchor_name} {RELATION_LABELS.get(relation_type, relation_type)} {entity_name}",
                        "confidence": confidence,
                    }
                )
        return inferred

    def _llm_available(self) -> bool:
        if self.llm is None:
            try:
                self.llm = LocalLLM(self.llm_config)
            except (RuntimeError, OSError, TypeError, ValueError):
                self.llm = None
        if self.llm is None:
            return False
        try:
            info = self._run_with_timeout(self.llm.get_model_info, 1.5)
        except (RuntimeError, OSError, TypeError, ValueError, AttributeError):
            return False
        if not isinstance(info, dict):
            return False
        return bool(info.get("available", False))

    def _parse_llm_json(self, response: str) -> dict[str, Any]:
        match = re.search(r"\{[\s\S]*\}", response)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    def _llm_extract(self, text: str) -> GraphExtractionResult:
        if not self._llm_available():
            return GraphExtractionResult([], [], "rule")
        prompt = f"""
你是知识图谱抽取器。请从下面文本抽取实体和关系，只返回 JSON。

要求：
1. 实体字段：name, type, aliases, description, importance
2. 关系字段：subject, type, object, strength, description, confidence
3. type 只允许这些实体类型：
人物 person / 工具 tool / 概念 concept / 项目 project / 组织 organization /
技术 technology / 事件 event / 资源 resource / 配置 configuration / 决策 decision
4. 关系 type 只允许：
depends_on, part_of, uses, similar_to, replaces, creates, belongs_to, related_to,
causes, contradicts, evolves_from, learns_from
5. 如果不确定，尽量少抽，避免编造。

文本：
\"\"\"{text[:4000]}\"\"\"

输出 JSON 格式：
{{
  "entities": [
    {{"name": "...", "type": "tool", "aliases": [], "description": "...", "importance": 0.7}}
  ],
  "relations": [
    {{"subject": "...", "type": "uses", "object": "...", "strength": 0.7, "description": "...", "confidence": 0.7}}
  ]
}}
""".strip()
        if self.llm is None:
            return GraphExtractionResult([], [], "rule")
        try:
            response = self._run_with_timeout(
                lambda: self._run_async(
                    self.llm.chat(
                        [{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=1200,
                        task_type="complex",
                        use_batch=False,
                    )
                ),
                8.0,
            )
        except (RuntimeError, OSError, TypeError, ValueError, AttributeError):
            response = None
        if not isinstance(response, str) or not response.strip():
            return GraphExtractionResult([], [], "rule")
        data = self._parse_llm_json(response if isinstance(response, str) else "")
        entities = data.get("entities", []) if isinstance(data, dict) else []
        relations = data.get("relations", []) if isinstance(data, dict) else []
        clean_entities = []
        for entity in entities:
            name = self._normalize_candidate(str(entity.get("name", "")))
            if not self._is_meaningful_name(name):
                continue
            clean_entities.append(
                {
                    "name": name,
                    "type": entity.get("type", self._infer_entity_type(name, text)),
                    "aliases": entity.get("aliases", []) or [],
                    "description": str(entity.get("description", "")).strip(),
                    "importance": max(0.0, min(1.0, float(entity.get("importance", 0.65)))),
                }
            )
        clean_relations = []
        for relation in relations:
            subject = self._normalize_candidate(str(relation.get("subject", "")))
            object_name = self._normalize_candidate(str(relation.get("object", "")))
            if not self._is_meaningful_name(subject) or not self._is_meaningful_name(object_name):
                continue
            clean_relations.append(
                {
                    "subject": subject,
                    "type": relation.get("type", "related_to"),
                    "object": object_name,
                    "strength": max(0.0, min(1.0, float(relation.get("strength", 0.65)))),
                    "description": str(relation.get("description", "")).strip(),
                    "confidence": max(0.0, min(1.0, float(relation.get("confidence", 0.65)))),
                }
            )
        return GraphExtractionResult(clean_entities, clean_relations, "llm")

    def extract_knowledge(self, text: str, use_llm: bool | None = None) -> GraphExtractionResult:
        mode = str(self.settings["extraction_mode"]).lower()
        rule_entities = self._rule_extract_entities(text)
        rule_relations = self._rule_extract_relations(text)
        rule_relations.extend(self._infer_context_relations(rule_entities, rule_relations, text))
        if use_llm is None:
            use_llm = mode in {"llm", "hybrid"}
        if mode == "rule" or not use_llm:
            return GraphExtractionResult(rule_entities, rule_relations, "rule")
        llm_result = self._llm_extract(text)
        if mode == "llm":
            return llm_result
        merged_entities: dict[str, dict[str, Any]] = {}
        for item in rule_entities + llm_result.entities:
            key = self._normalize_name(item["name"])
            existing = merged_entities.get(key)
            if existing is None:
                merged_entities[key] = dict(item)
                continue
            existing["importance"] = max(float(existing.get("importance", 0.5)), float(item.get("importance", 0.5)))
            aliases = set(existing.get("aliases", [])) | set(item.get("aliases", []))
            existing["aliases"] = sorted(aliases)
            if item.get("description") and item["description"] not in existing.get("description", ""):
                existing["description"] = (existing.get("description", "") + " " + item["description"]).strip()
            if existing.get("type") == "concept" and item.get("type") != "concept":
                existing["type"] = item["type"]
        merged_relations: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in rule_relations + llm_result.relations:
            rel_key = (
                self._normalize_name(item["subject"]),
                item["type"],
                self._normalize_name(item["object"]),
            )
            existing = merged_relations.get(rel_key)
            if existing is None:
                merged_relations[rel_key] = dict(item)
                continue
            existing["strength"] = max(float(existing.get("strength", 0.3)), float(item.get("strength", 0.3)))
            existing["confidence"] = max(float(existing.get("confidence", 0.3)), float(item.get("confidence", 0.3)))
            if item.get("description") and item["description"] not in existing.get("description", ""):
                existing["description"] = (existing.get("description", "") + " " + item["description"]).strip()
        return GraphExtractionResult(
            list(merged_entities.values()),
            list(merged_relations.values()),
            "hybrid",
        )

    def _should_use_llm_for_content(self, content: str, title: str = "", source: str = "") -> bool:
        mode = str(self.settings["extraction_mode"]).lower()
        if mode not in {"llm", "hybrid"}:
            return False
        probe = f"{source} {title} {content[:1800]}".casefold()
        if any(hint in probe for hint in TECH_ENTITY_HINTS):
            return True
        if any(
            token in probe
            for token in (
                "arxiv",
                "benchmark",
                "github",
                "api",
                "openclaw",
                "ollama",
                "deepseek",
                "sqlite",
                "whoosh",
            )
        ):
            return True
        return False

    def ingest_memory(
        self,
        memory_ref: str,
        content: str,
        source: str,
        title: str = "",
        use_llm: bool | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if not self.is_enabled():
            return {"status": "disabled"}
        if not content.strip():
            return {"status": "skipped", "reason": "empty"}
        memory_hash = self._compute_memory_hash(content)
        if not force and not self._needs_extraction(memory_ref, memory_hash):
            return {"status": "skipped", "reason": "already_extracted"}

        extraction = self.extract_knowledge(content, use_llm=use_llm)
        entity_ids: list[int] = []
        relation_ids: list[int] = []
        snippet = self._clean_sentence_for_display(content)
        related_names = {
            self._normalize_name(name)
            for relation in extraction.relations
            for name in (relation["subject"], relation["object"])
        }
        for entity in extraction.entities:
            entity_quality = float(entity.get("quality", self._entity_quality_score(entity["name"])))
            entity_frequency = int(entity.get("frequency", 1))
            entity_key = self._normalize_name(entity["name"])
            entity_type = entity.get("type", "concept")
            strong_standalone = (
                entity_key in related_names
                or entity_quality >= self._q("ingest_strong_quality_threshold", 0.88)
                or entity_frequency >= int(self._q("ingest_min_frequency", 2.0))
                or (
                    entity_type
                    in {
                        "tool",
                        "technology",
                        "organization",
                        "resource",
                        "configuration",
                        "project",
                    }
                    and entity_quality >= self._q("ingest_typed_quality_threshold", 0.68)
                )
            )
            if not strong_standalone:
                continue
            entity_description = entity.get("description", "") or self._derive_entity_description(
                entity["name"], content, title=title
            )
            stored = self.add_entity(
                entity["name"],
                entity_type=entity_type,
                aliases=entity.get("aliases", []),
                description=entity_description,
                importance=entity.get("importance", 0.5),
                source_memory_ref=memory_ref,
            )
            if not stored:
                continue
            entity_ids.append(int(stored["id"]))
            entity_snippet = entity_description or snippet
            self._upsert_anchor(memory_ref, source, title, memory_hash, entity_snippet, entity_id=int(stored["id"]))
        for relation in extraction.relations:
            stored = self.add_relation(
                relation["subject"],
                relation["type"],
                relation["object"],
                strength=relation.get("strength", 0.5),
                description=relation.get("description", ""),
                source_memory_ref=memory_ref,
                confidence=relation.get("confidence", 0.5),
            )
            if not stored:
                continue
            relation_ids.append(int(stored["id"]))
            relation_snippet = self._clean_sentence_for_display(relation.get("description", "") or snippet)
            self._upsert_anchor(
                memory_ref,
                source,
                title,
                memory_hash,
                relation_snippet,
                relation_id=int(stored["id"]),
            )
        self._mark_memory_extracted(memory_ref, source, title, memory_hash, extraction.mode)
        return {
            "status": "success",
            "memory_ref": memory_ref,
            "entities_added": len(set(entity_ids)),
            "relations_added": len(set(relation_ids)),
            "mode": extraction.mode,
        }

    def batch_extract_pending_memories(self, limit: int | None = None, force: bool = False) -> dict[str, Any]:
        limit = limit or int(self.settings["batch_size"])
        pending: list[tuple[str, str, str, str]] = []
        memory_md = self.config["memory_md"]
        if memory_md.exists():
            memory_text = memory_md.read_text(encoding="utf-8")
            memory_text = (
                memory_text.replace("# MEMORY.md - Your Long-Term Memory", "")
                .replace(
                    "This is your curated long-term memory. Edit freely!",
                    "",
                )
                .strip()
            )
            pending.append(("file::MEMORY.md", memory_text, "MEMORY.md", "Long-term Memory"))
        blocks_file = self.config["memory_dir"] / "memory_blocks.json"
        if blocks_file.exists():
            try:
                blocks = json.loads(blocks_file.read_text(encoding="utf-8"))
            except (TypeError, ValueError, json.JSONDecodeError):
                blocks = {}
            archival = str(blocks.get("archival", "")).strip()
            if archival:
                pending.append(
                    (
                        "file::memory_blocks:archival",
                        archival,
                        "memory_blocks:archival",
                        "Archival Memory Block",
                    )
                )
        for log_file in sorted(
            list_daily_log_files(self.config["memory_dir"], self.config.get("daily_dir")),
            reverse=True,
        ):
            pending.append(
                (
                    f"file::{log_file.name}",
                    log_file.read_text(encoding="utf-8"),
                    f"daily_log:{log_file.name}",
                    f"Daily Log - {log_file.stem}",
                )
            )
        processed = 0
        results: list[dict[str, Any]] = []
        for memory_ref, content, source, title in pending:
            if processed >= limit:
                break
            memory_hash = self._compute_memory_hash(content)
            if not force and not self._needs_extraction(memory_ref, memory_hash):
                continue
            # Batch rebuild favors deterministic extraction so historical rebuilds stay fast and stable.
            results.append(self.ingest_memory(memory_ref, content, source, title=title, use_llm=False, force=force))
            processed += 1
        self.refine_related_relations()
        self.recompute_importance_scores()
        self.prune_low_signal_related_entities()
        self.prune_weak_isolated_entities()
        return {
            "status": "success",
            "processed": processed,
            "results": results,
        }

    def _search_anchor_rows(self, entity_ids: Iterable[int], limit: int) -> list[sqlite3.Row]:
        entity_ids = list({int(entity_id) for entity_id in entity_ids})
        if not entity_ids:
            return []
        placeholders = ",".join("?" for _ in entity_ids)
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT a.memory_ref, a.source, a.title, a.memory_hash, a.snippet,
                       e.canonical_name, e.importance, e.access_count
                FROM memory_anchors a
                JOIN entities e ON e.id = a.entity_id
                WHERE a.entity_id IN ({placeholders})
                ORDER BY e.importance DESC, e.access_count DESC, a.created_at DESC
                LIMIT ?
                """,
                tuple(entity_ids + [limit]),
            ).fetchall()

    def _search_anchor_snippets(self, query: str, limit: int) -> list[sqlite3.Row]:
        cost_tokens = [token for token in ("便宜", "价格", "低价", "成本", "免费", "零成本") if token in query]
        tokens: list[str] = []
        for token in re.findall(r"[A-Za-z0-9_.:+/-]{2,}|[\u4e00-\u9fff]{2,}", query):
            token = token.strip()
            if not token:
                continue
            tokens.append(token)
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", token):
                max_width = min(4, len(token))
                for width in range(2, max_width + 1):
                    for start in range(0, len(token) - width + 1):
                        tokens.append(token[start : start + width])
        filtered_tokens: list[str] = []
        for token in dict.fromkeys(tokens):
            if token in STOP_QUERY_TOKENS:
                continue
            if len(token) == 2 and token not in (
                "便宜",
                "模型",
                "技能",
                "工具",
                "系统",
                "记忆",
                "配置",
                "端口",
                "路径",
            ):
                continue
            filtered_tokens.append(token)
        tokens = filtered_tokens
        if not tokens:
            return []
        specific_tokens = [token for token in tokens if token not in GENERIC_QUERY_TOKENS]
        clauses = []
        params: list[Any] = []
        max_query_tokens = int(self._q("search_snippet_max_query_tokens", 5.0))
        for token in tokens[:max_query_tokens]:
            clauses.append("a.snippet LIKE ?")
            params.append(f"%{token}%")
        row_fetch_multiplier = int(self._q("search_snippet_row_fetch_multiplier", 12.0))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT a.memory_ref, a.source, a.title, a.memory_hash, a.snippet,
                       e.canonical_name, e.importance, e.access_count
                FROM memory_anchors a
                JOIN entities e ON e.id = a.entity_id
                WHERE {" OR ".join(clauses)}
                LIMIT ?
                """,
                tuple(params + [limit * row_fetch_multiplier]),
            ).fetchall()
        ranked: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            snippet = row["snippet"] or ""
            quality = self._entity_quality_score(row["canonical_name"])
            if quality < self._q("search_snippet_entity_min_quality", 0.4):
                continue
            match_score = 0.0
            specific_hit = not specific_tokens
            cost_hit = not cost_tokens
            token_len_divisor = self._q("search_snippet_token_len_divisor", 3.0)
            token_score_cap = self._q("search_snippet_token_score_cap", 1.0)
            for token in tokens:
                if token in snippet:
                    match_score += min(token_score_cap, len(token) / max(1e-6, token_len_divisor))
                    if token in specific_tokens:
                        specific_hit = True
                    if token in cost_tokens:
                        cost_hit = True
            if not specific_hit:
                continue
            if not cost_hit:
                continue
            match_score += float(row["importance"]) * self._q("search_snippet_importance_factor", 0.25)
            match_score += min(
                self._q("search_snippet_access_cap", 0.3),
                float(row["access_count"]) * self._q("search_snippet_access_factor", 0.02),
            )
            match_score += quality * self._q("search_snippet_quality_factor", 0.5)
            if re.search(r"[#|*_]{2,}", snippet):
                match_score -= self._q("search_snippet_markup_penalty", 0.6)
            ranked.append((match_score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in ranked[:limit]]

    def search_related_memories(self, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or int(self.settings["default_return_limit"])
        primary_limit = int(self._q("related_primary_match_limit", 5.0))
        matched = self.search_entities(query, limit=min(limit, primary_limit))
        if not matched:
            for candidate in self._candidate_entities_from_text(query):
                matched = self.search_entities(candidate, limit=min(limit, primary_limit))
                if matched:
                    break
        if not matched:
            return []
        touched_entity_ids = {int(entity["id"]) for entity in matched if entity.get("id") is not None}

        anchor_scores: dict[str, dict[str, Any]] = {}
        direct_entity_ids = [int(entity["id"]) for entity in matched]
        direct_fetch_multiplier = int(self._q("related_direct_fetch_multiplier", 4.0))
        direct_importance_factor = self._q("related_direct_importance_factor", 0.72)
        direct_access_cap = self._q("related_direct_access_cap", 0.28)
        direct_access_factor = self._q("related_direct_access_factor", 0.02)
        for row in self._search_anchor_rows(direct_entity_ids, limit * direct_fetch_multiplier):
            score = direct_importance_factor * float(row["importance"]) + min(
                direct_access_cap, float(row["access_count"]) * direct_access_factor
            )
            payload = anchor_scores.setdefault(
                row["memory_ref"],
                {
                    "content": row["snippet"] or "",
                    "source": row["source"],
                    "title": row["title"] or row["source"],
                    "score": 0.0,
                    "search_type": "knowledge_graph",
                    "matched_entities": [],
                    "recommended_by": [],
                },
            )
            payload["score"] = max(payload["score"], score)
            if row["canonical_name"] not in payload["matched_entities"]:
                payload["matched_entities"].append(row["canonical_name"])

        for entity in matched:
            related_limit = int(self._q("related_secondary_entity_limit", 6.0))
            related = self.related_entities(entity["canonical_name"], limit=min(limit, related_limit))
            related_map = {int(item["entity"]["id"]): item for item in related}
            related_ids = [int(item["entity"]["id"]) for item in related]
            touched_entity_ids.update(related_ids)
            for row in self._search_anchor_rows(related_ids, limit * direct_fetch_multiplier):
                related_row_entity_id = self._resolve_entity_id(row["canonical_name"])
                related_entity = (
                    related_map.get(int(related_row_entity_id)) if related_row_entity_id is not None else None
                )
                score = self._q("related_indirect_base_score", 0.32) + self._q(
                    "related_indirect_importance_factor", 0.45
                ) * float(row["importance"])
                if related_entity:
                    score += min(
                        self._q("related_indirect_relatedness_cap", 0.23),
                        float(related_entity["relatedness"]),
                    )
                payload = anchor_scores.setdefault(
                    row["memory_ref"],
                    {
                        "content": row["snippet"] or "",
                        "source": row["source"],
                        "title": row["title"] or row["source"],
                        "score": 0.0,
                        "search_type": "knowledge_graph",
                        "matched_entities": [],
                        "recommended_by": [],
                    },
                )
                payload["score"] = max(payload["score"], score)
                if entity["canonical_name"] not in payload["recommended_by"]:
                    payload["recommended_by"].append(entity["canonical_name"])

        ranked = sorted(anchor_scores.values(), key=lambda item: item["score"], reverse=True)
        # Retrieval touches should feed entity access statistics so importance/cleanup can use real usage evidence.
        for entity_id in sorted(touched_entity_ids):
            self.touch_entity_access(entity_id)
        return ranked[:limit]

    def build_context_for_message(self, message: str, limit: int | None = None) -> dict[str, Any]:
        if not self.settings.get("context_injection_enabled", True):
            return {"enabled": False, "text": "", "entities": []}
        limit = limit or int(self.settings["context_injection_limit"])
        query_has_cost_signal = any(token in message for token in ("便宜", "价格", "低价", "成本", "免费"))
        context_primary_limit = int(self._q("context_primary_match_limit", 4.0))
        matched = self.search_entities(message, limit=min(limit, context_primary_limit))
        if not matched:
            for candidate in self._candidate_entities_from_text(message):
                matched = self.search_entities(candidate, limit=min(limit, context_primary_limit))
                if matched:
                    break
        if not matched:
            for generic_keyword in (
                "模型",
                "工具",
                "技能",
                "配置",
                "记忆",
                "图谱",
                "OpenClaw",
                "Ollama",
            ):
                if generic_keyword in message:
                    matched = self.search_entities(generic_keyword, limit=min(limit, context_primary_limit))
                    if matched:
                        break
        anchor_rows = self._search_anchor_snippets(message, limit)
        if query_has_cost_signal and anchor_rows:
            lines = ["相关知识："]
            entities: list[str] = []
            for row in anchor_rows[:limit]:
                if row["canonical_name"] not in entities:
                    entities.append(row["canonical_name"])
                lines.append(f"- {row['canonical_name']}：{(row['snippet'] or '')[:120]}")
            return {"enabled": True, "text": "\n".join(lines), "entities": entities}
        if not matched:
            if not anchor_rows:
                return {"enabled": True, "text": "", "entities": []}
            lines = ["相关知识："]
            entities = []
            for row in anchor_rows[:limit]:
                if row["canonical_name"] not in entities:
                    entities.append(row["canonical_name"])
                lines.append(f"- {row['canonical_name']}：{(row['snippet'] or '')[:120]}")
            return {"enabled": True, "text": "\n".join(lines), "entities": entities}

        lines = ["相关知识："]
        relation_lines = []
        entity_names: list[str] = []
        for entity in matched[:limit]:
            entity_names.append(entity["canonical_name"])
            self.touch_entity_access(int(entity["id"]))
            description = (
                entity.get("description", "").strip()
                or f"{ENTITY_TYPE_LABELS.get(entity['entity_type'], entity['entity_type'])}实体"
            )
            lines.append(
                f"- {entity['canonical_name']}（"
                f"{ENTITY_TYPE_LABELS.get(entity['entity_type'], entity['entity_type'])}"
                f"）：{description}"
            )
            for relation in self.list_relations(entity["canonical_name"], limit=2):
                if str(relation.get("relation_status", "stable")) in {
                    "suspect",
                    "noisy",
                    "archived_candidate",
                }:
                    continue
                relation_lines.append(
                    f"- {relation['subject_name']} "
                    f"{RELATION_LABELS.get(relation['relation_type'], relation['relation_type'])} "
                    f"{relation['object_name']}"
                )
        if relation_lines:
            lines.append("关联关系：")
            lines.extend(relation_lines[:limit])
        return {
            "enabled": True,
            "text": "\n".join(lines),
            "entities": entity_names,
        }

    def timeline(self, days: int = 7, limit: int | None = None) -> dict[str, Any]:
        limit = limit or int(self.settings["default_return_limit"])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            entities = conn.execute(
                """
                SELECT canonical_name AS name, entity_type AS type, created_at, updated_at
                FROM entities
                WHERE updated_at >= ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
            relations = conn.execute(
                """
                SELECT s.canonical_name AS subject_name,
                       relation_type,
                       o.canonical_name AS object_name,
                       r.created_at AS created_at,
                       r.updated_at AS updated_at
                FROM relations r
                JOIN entities s ON s.id = r.subject_entity_id
                JOIN entities o ON o.id = r.object_entity_id
                WHERE r.updated_at >= ?
                ORDER BY r.updated_at DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        return {
            "days": days,
            "entities": [dict(row) for row in entities],
            "relations": [dict(row) for row in relations],
        }

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            entity_total = conn.execute("SELECT COUNT(*) AS total FROM entities").fetchone()["total"]
            relation_total = conn.execute("SELECT COUNT(*) AS total FROM relations").fetchone()["total"]
            entities_by_type = conn.execute(
                "SELECT entity_type, COUNT(*) AS total FROM entities GROUP BY entity_type ORDER BY total DESC"
            ).fetchall()
            relations_by_type = conn.execute(
                "SELECT relation_type, COUNT(*) AS total FROM relations GROUP BY relation_type ORDER BY total DESC"
            ).fetchall()
            top_entities = conn.execute(
                """
                SELECT canonical_name, entity_type, importance, access_count
                FROM entities
                ORDER BY importance DESC, access_count DESC
                LIMIT 10
                """
            ).fetchall()
            hubs = conn.execute(
                """
                SELECT e.canonical_name,
                       COUNT(r.id) AS relation_count
                FROM entities e
                LEFT JOIN relations r
                  ON r.subject_entity_id = e.id OR r.object_entity_id = e.id
                GROUP BY e.id
                ORDER BY relation_count DESC, e.importance DESC
                LIMIT 10
                """
            ).fetchall()
            recent_entities = conn.execute(
                """
                SELECT canonical_name, entity_type, created_at
                FROM entities
                ORDER BY created_at DESC
                LIMIT 10
                """
            ).fetchall()
            isolated = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM entities e
                WHERE NOT EXISTS (
                    SELECT 1 FROM relations r
                    WHERE r.subject_entity_id = e.id OR r.object_entity_id = e.id
                )
                """
            ).fetchone()["total"]
            relation_rows = conn.execute(
                """
                SELECT id, relation_type, confidence, access_count, source_memory_ref, description, updated_at
                FROM relations
                """
            ).fetchall()
        quality_state = self._load_quality_state()
        status_counts = {"stable": 0, "suspect": 0, "noisy": 0, "archived_candidate": 0}
        soft_isolated = 0
        for row in relation_rows:
            item = dict(row)
            q = self._relation_quality_status(item, state=quality_state)
            status = str(q.get("relation_status", "stable"))
            if status not in status_counts:
                status = "stable"
            status_counts[status] += 1
            if bool(q.get("soft_isolated", False)):
                soft_isolated += 1
        low_quality = status_counts["suspect"] + status_counts["noisy"]
        low_quality_ratio = round(low_quality / max(1, int(relation_total)), 4)
        return {
            "entity_total": entity_total,
            "relation_total": relation_total,
            "entities_by_type": [dict(row) for row in entities_by_type],
            "relations_by_type": [dict(row) for row in relations_by_type],
            "top_entities": [dict(row) for row in top_entities],
            "knowledge_hubs": [dict(row) for row in hubs],
            "recent_entities": [dict(row) for row in recent_entities],
            "isolated_entities": isolated,
            "relation_status_counts": status_counts,
            "soft_isolated_relations": soft_isolated,
            "low_quality_relation_ratio": low_quality_ratio,
        }

    def recompute_importance_scores(self) -> dict[str, Any]:
        updated = 0
        with self._connect() as conn:
            entities = conn.execute(
                """
                SELECT e.id, e.canonical_name, e.entity_type, e.access_count, e.updated_at,
                       e.importance, COUNT(DISTINCT a.id) AS anchor_count,
                       COUNT(DISTINCT r.id) AS relation_count
                FROM entities e
                LEFT JOIN memory_anchors a ON a.entity_id = e.id
                LEFT JOIN relations r ON r.subject_entity_id = e.id OR r.object_entity_id = e.id
                GROUP BY e.id
                """
            ).fetchall()
            now = datetime.now(timezone.utc)
            for row in entities:
                quality = self._entity_quality_score(row["canonical_name"])
                relation_factor = min(0.35, float(row["relation_count"]) * self._q("importance_relation_factor", 0.07))
                access_factor = min(0.18, float(row["access_count"]) * self._q("importance_access_factor", 0.03))
                anchor_factor = min(0.14, float(row["anchor_count"]) * self._q("importance_anchor_factor", 0.03))
                updated_at = self._parse_utc_datetime(row["updated_at"]) or now
                recency_days = max(0.0, (now - updated_at).total_seconds() / 86400)
                recency_cap = self._q("importance_recency_max_bonus", 0.12)
                recency_decay = self._q("importance_recency_daily_decay", 0.002)
                recency_factor = max(0.0, recency_cap - min(recency_cap, recency_days * recency_decay))
                type_bonus = 0.0
                if row["entity_type"] in {"tool", "technology", "organization"}:
                    type_bonus = 0.08
                elif row["entity_type"] in {"resource", "project"}:
                    type_bonus = 0.05
                base_value = self._q("importance_base_value", 0.28)
                quality_factor = self._q("importance_quality_factor", 0.28)
                new_importance = max(
                    0.3,
                    min(
                        0.98,
                        base_value
                        + quality * quality_factor
                        + relation_factor
                        + access_factor
                        + anchor_factor
                        + recency_factor
                        + type_bonus,
                    ),
                )
                if abs(new_importance - float(row["importance"])) >= 0.01:
                    conn.execute(
                        "UPDATE entities SET importance = ?, updated_at = ? WHERE id = ?",
                        (new_importance, self._utcnow(), row["id"]),
                    )
                    updated += 1
            conn.commit()
        return {"status": "success", "updated": updated}

    def refine_related_relations(self) -> dict[str, Any]:
        converted = 0
        removed = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.subject_entity_id, r.object_entity_id, r.description, r.strength, r.confidence,
                       s.canonical_name AS subject_name, s.entity_type AS subject_type,
                       o.canonical_name AS object_name, o.entity_type AS object_type
                FROM relations r
                JOIN entities s ON s.id = r.subject_entity_id
                JOIN entities o ON o.id = r.object_entity_id
                WHERE r.relation_type = 'related_to'
                """
            ).fetchall()
            for row in rows:
                description = (row["description"] or "").casefold()
                subject_name = row["subject_name"]
                object_name = row["object_name"]
                subject_type = row["subject_type"]
                object_type = row["object_type"]
                target_type = None

                if any(token in description for token in ("使用", "采用", "接入", "支持", "集成", "调用", "部署")):
                    target_type = "uses"
                elif any(token in description for token in ("依赖", "需要", "基于", "建立在", "配置", "认证")):
                    target_type = "depends_on"
                elif subject_name in object_name or object_name in subject_name:
                    target_type = "part_of"
                elif subject_type == "organization" and self._model_like_name(object_name):
                    target_type = "belongs_to"
                elif object_type == "organization" and self._model_like_name(subject_name):
                    target_type = "belongs_to"

                if not target_type or target_type == "related_to":
                    continue

                duplicate = conn.execute(
                    """
                    SELECT id, strength, confidence FROM relations
                    WHERE subject_entity_id = ? AND object_entity_id = ? AND relation_type = ?
                    """,
                    (row["subject_entity_id"], row["object_entity_id"], target_type),
                ).fetchone()
                if duplicate:
                    conn.execute(
                        """
                        UPDATE relations
                        SET strength = ?, confidence = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            max(float(duplicate["strength"]), float(row["strength"])),
                            max(float(duplicate["confidence"]), float(row["confidence"])),
                            self._utcnow(),
                            duplicate["id"],
                        ),
                    )
                    conn.execute(
                        "UPDATE memory_anchors SET relation_id = ? WHERE relation_id = ?",
                        (duplicate["id"], row["id"]),
                    )
                    conn.execute("DELETE FROM relations WHERE id = ?", (row["id"],))
                    removed += 1
                else:
                    conn.execute(
                        "UPDATE relations SET relation_type = ?, updated_at = ? WHERE id = ?",
                        (target_type, self._utcnow(), row["id"]),
                    )
                    converted += 1
            conn.commit()
        return {"status": "success", "converted": converted, "removed": removed}

    def merge_entities(self, keep_name: str, remove_name: str) -> dict[str, Any]:
        keep = self.get_entity(keep_name)
        remove = self.get_entity(remove_name)
        if not keep or not remove:
            return {"status": "not_found"}
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE relations SET subject_entity_id = ? WHERE subject_entity_id = ?",
                (keep["id"], remove["id"]),
            )
            cursor.execute(
                "UPDATE relations SET object_entity_id = ? WHERE object_entity_id = ?",
                (keep["id"], remove["id"]),
            )
            cursor.execute(
                "UPDATE memory_anchors SET entity_id = ? WHERE entity_id = ?",
                (keep["id"], remove["id"]),
            )
            cursor.execute(
                """
                INSERT OR IGNORE INTO entity_aliases (entity_id, alias, alias_key, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (keep["id"], remove["canonical_name"], remove["name_key"], self._utcnow()),
            )
            for alias in self._list_aliases(int(remove["id"])):
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO entity_aliases (entity_id, alias, alias_key, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (keep["id"], alias, self._normalize_name(alias), self._utcnow()),
                )
            cursor.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (remove["id"],))
            cursor.execute("DELETE FROM entities WHERE id = ?", (remove["id"],))
            conn.commit()
        return {
            "status": "success",
            "kept": keep["canonical_name"],
            "removed": remove["canonical_name"],
        }

    def decay_relation_weights(self) -> dict[str, Any]:
        decay_rate = max(0.0, min(1.0, float(self.settings["daily_decay_rate"])))
        min_strength = float(self.settings["min_relation_strength"])
        with self._connect() as conn:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT id, strength FROM relations").fetchall()
            updated = 0
            deleted = 0
            for row in rows:
                new_strength = float(row["strength"]) * (1.0 - decay_rate)
                if new_strength < min_strength:
                    cursor.execute("DELETE FROM memory_anchors WHERE relation_id = ?", (row["id"],))
                    cursor.execute("DELETE FROM relations WHERE id = ?", (row["id"],))
                    deleted += 1
                else:
                    cursor.execute(
                        "UPDATE relations SET strength = ?, updated_at = ? WHERE id = ?",
                        (new_strength, self._utcnow(), row["id"]),
                    )
                    updated += 1
            conn.commit()
        return {"status": "success", "updated": updated, "deleted": deleted}

    def backfill_entity_access_from_anchors(self, min_access: int = 1) -> dict[str, Any]:
        """One-time remediation: entities referenced by anchors should not stay at zero access forever."""
        min_access = max(1, int(min_access))
        with self._connect() as conn:
            before = conn.execute("SELECT COUNT(*) AS total FROM entities WHERE access_count = 0").fetchone()["total"]
            conn.execute(
                """
                UPDATE entities
                SET access_count = ?,
                    updated_at = ?
                WHERE access_count = 0
                  AND id IN (
                      SELECT DISTINCT entity_id FROM memory_anchors
                      WHERE entity_id IS NOT NULL
                  )
                """,
                (min_access, self._utcnow()),
            )
            conn.commit()
            after = conn.execute("SELECT COUNT(*) AS total FROM entities WHERE access_count = 0").fetchone()["total"]
        return {
            "status": "success",
            "zero_access_before": int(before),
            "zero_access_after": int(after),
            "updated_entities": max(0, int(before) - int(after)),
        }

    def cleanup_isolated_entities(self) -> dict[str, Any]:
        retention_days = int(self.settings["isolated_entity_retention_days"])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.canonical_name
                FROM entities e
                WHERE e.updated_at < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM relations r
                      WHERE r.subject_entity_id = e.id OR r.object_entity_id = e.id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM memory_anchors a
                      WHERE a.entity_id = e.id
                  )
                """,
                (cutoff,),
            ).fetchall()
            removed = [row["canonical_name"] for row in rows]
            for row in rows:
                conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (row["id"],))
                conn.execute("DELETE FROM entities WHERE id = ?", (row["id"],))
            conn.commit()
        return {"status": "success", "removed": removed}

    def prune_weak_isolated_entities(self) -> dict[str, Any]:
        removed: list[str] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.canonical_name, e.entity_type, e.importance, e.access_count, e.description
                FROM entities e
                WHERE NOT EXISTS (
                    SELECT 1 FROM relations r
                    WHERE r.subject_entity_id = e.id OR r.object_entity_id = e.id
                )
                """
            ).fetchall()
            for row in rows:
                quality = self._entity_quality_score(row["canonical_name"])
                description_score = self._entity_description_score(
                    row["description"] or "", entity_name=row["canonical_name"]
                )
                normalized = self._normalize_name(row["canonical_name"])
                if row["entity_type"] == "resource" and int(row["access_count"]) == 0:
                    conn.execute("DELETE FROM memory_anchors WHERE entity_id = ?", (row["id"],))
                    conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (row["id"],))
                    conn.execute("DELETE FROM entities WHERE id = ?", (row["id"],))
                    removed.append(row["canonical_name"])
                    continue
                if row["entity_type"] == "tool" and (
                    normalized.startswith("openclaw_")
                    or "运行中" in (row["description"] or "")
                    or "每天" in (row["description"] or "")
                    or "每周" in (row["description"] or "")
                ):
                    conn.execute("DELETE FROM memory_anchors WHERE entity_id = ?", (row["id"],))
                    conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (row["id"],))
                    conn.execute("DELETE FROM entities WHERE id = ?", (row["id"],))
                    removed.append(row["canonical_name"])
                    continue
                if row["entity_type"] in {
                    "tool",
                    "technology",
                    "organization",
                    "resource",
                } and quality >= self._q("prune_weak_isolated_quality_keep", 0.72):
                    continue
                if row["entity_type"] == "concept" and any(
                    token in normalized
                    for token in (
                        "主力模型",
                        "推理模型",
                        "复杂模型",
                        "智能模型",
                        "当前本地模型",
                        "检查模型配置",
                        "当前模型配置",
                        "推荐模型",
                        "模型选择与配置",
                        "商业化模型",
                        "本地部署和开源模型",
                        "开源训练数据和模型",
                        "定价模型",
                        "替换或提升配置文件中的本地模型",
                        "as备用模型",
                    )
                ):
                    conn.execute("DELETE FROM memory_anchors WHERE entity_id = ?", (row["id"],))
                    conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (row["id"],))
                    conn.execute("DELETE FROM entities WHERE id = ?", (row["id"],))
                    removed.append(row["canonical_name"])
                    continue
                if self._model_like_name(row["canonical_name"]) and quality >= self._q(
                    "prune_model_like_quality_keep", 0.68
                ):
                    if any(
                        token in normalized
                        for token in (
                            "deepseek v3",
                            "qwen3.5",
                            "deepseek-r1",
                            "minimax",
                            "gpt",
                            "llama",
                            "gemma",
                        )
                    ):
                        continue
                    if re.fullmatch(r"(?:qwen|deepseek|chatgpt)\s+\d+", normalized):
                        conn.execute("DELETE FROM memory_anchors WHERE entity_id = ?", (row["id"],))
                        conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (row["id"],))
                        conn.execute("DELETE FROM entities WHERE id = ?", (row["id"],))
                        removed.append(row["canonical_name"])
                        continue
                    continue
                if row["entity_type"] == "concept" and quality < self._q("prune_concept_quality_min", 0.9):
                    conn.execute("DELETE FROM memory_anchors WHERE entity_id = ?", (row["id"],))
                    conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (row["id"],))
                    conn.execute("DELETE FROM entities WHERE id = ?", (row["id"],))
                    removed.append(row["canonical_name"])
                    continue
                if (
                    float(row["importance"]) >= self._q("prune_importance_keep", 0.82)
                    and quality >= self._q("prune_model_like_quality_keep", 0.68)
                    and description_score >= self._q("prune_description_keep", 0.1)
                ):
                    continue
                if float(row["access_count"]) > 0 and quality >= self._q("prune_access_quality_keep", 0.62):
                    continue
                conn.execute("DELETE FROM memory_anchors WHERE entity_id = ?", (row["id"],))
                conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (row["id"],))
                conn.execute("DELETE FROM entities WHERE id = ?", (row["id"],))
                removed.append(row["canonical_name"])
            conn.commit()
        return {"status": "success", "removed_count": len(removed), "removed": removed[:50]}

    def prune_low_signal_related_entities(self) -> dict[str, Any]:
        removed: list[str] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.canonical_name, e.entity_type, e.access_count, e.description,
                       GROUP_CONCAT(DISTINCT r.relation_type) AS relation_types,
                       COUNT(DISTINCT r.id) AS relation_count
                FROM entities e
                JOIN relations r ON r.subject_entity_id = e.id OR r.object_entity_id = e.id
                GROUP BY e.id
                """
            ).fetchall()
            for row in rows:
                relation_types = set((row["relation_types"] or "").split(",")) if row["relation_types"] else set()
                quality = self._entity_quality_score(row["canonical_name"])
                description_score = self._entity_description_score(
                    row["description"] or "", entity_name=row["canonical_name"]
                )
                if relation_types != {"related_to"}:
                    continue
                if row["entity_type"] in {
                    "tool",
                    "technology",
                    "organization",
                    "resource",
                } and quality >= self._q("prune_related_type_quality_keep", 0.8):
                    continue
                if self._model_like_name(row["canonical_name"]):
                    continue
                if int(row["access_count"]) > 0:
                    continue
                if quality >= self._q("prune_high_quality_threshold", 0.78) and description_score >= self._q(
                    "prune_description_score_keep", 0.35
                ):
                    continue
                conn.execute("DELETE FROM memory_anchors WHERE entity_id = ?", (row["id"],))
                conn.execute(
                    "DELETE FROM memory_anchors WHERE relation_id IN "
                    "(SELECT id FROM relations WHERE subject_entity_id = ? "
                    "OR object_entity_id = ?)",
                    (row["id"], row["id"]),
                )
                conn.execute(
                    "DELETE FROM relations WHERE subject_entity_id = ? OR object_entity_id = ?",
                    (row["id"], row["id"]),
                )
                conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (row["id"],))
                conn.execute("DELETE FROM entities WHERE id = ?", (row["id"],))
                removed.append(row["canonical_name"])
            conn.commit()
        return {"status": "success", "removed_count": len(removed), "removed": removed[:50]}

    def health_check(self) -> dict[str, Any]:
        suspected_duplicates: list[dict[str, Any]] = []
        with self._connect() as conn:
            entities = conn.execute(
                "SELECT id, canonical_name, name_key FROM entities ORDER BY importance DESC"
            ).fetchall()
            broken_relations = conn.execute(
                """
                SELECT r.id
                FROM relations r
                LEFT JOIN entities s ON s.id = r.subject_entity_id
                LEFT JOIN entities o ON o.id = r.object_entity_id
                WHERE s.id IS NULL OR o.id IS NULL
                """
            ).fetchall()
            abnormal_weights = conn.execute(
                """
                SELECT id, strength
                FROM relations
                WHERE strength < 0 OR strength > 1.0
                """
            ).fetchall()

        for index, left in enumerate(entities):
            for right in entities[index + 1 : index + 8]:
                ratio = SequenceMatcher(None, left["name_key"], right["name_key"]).ratio()
                if ratio >= self._q("entity_health_duplicate_ratio", 0.92):
                    suspected_duplicates.append(
                        {
                            "left": left["canonical_name"],
                            "right": right["canonical_name"],
                            "similarity": ratio,
                        }
                    )
        return {
            "status": "success",
            "suspected_duplicates": suspected_duplicates[:20],
            "broken_relations": [dict(row) for row in broken_relations],
            "abnormal_relation_weights": [dict(row) for row in abnormal_weights],
        }

    def prepare_offline_cleanup(self, limit: int = 500) -> dict[str, Any]:
        """Prepare cleanup candidates and snapshot for rollback; no destructive mutation."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snapshot_dir = self.config["memory_dir"] / "cleanup_snapshots"
        report_dir = self.config["memory_dir"] / "cleanup_reports"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file = snapshot_dir / f"knowledge_graph_{ts}.db"
        report_file = report_dir / f"graph_cleanup_{ts}.json"
        shutil.copy2(self.db_path, snapshot_file)

        weak_rules: list[dict[str, Any]] = []
        merge_candidates: list[dict[str, Any]] = []
        anomalous_desc: list[dict[str, Any]] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.relation_type, r.strength, r.confidence, r.description,
                       r.access_count, r.updated_at, r.source_memory_ref,
                       s.canonical_name AS subject_name, o.canonical_name AS object_name
                FROM relations r
                JOIN entities s ON s.id = r.subject_entity_id
                JOIN entities o ON o.id = r.object_entity_id
                ORDER BY r.updated_at ASC
                LIMIT ?
                """,
                (max(limit, 2000),),
            ).fetchall()
        state = self._load_quality_state()
        for row in rows:
            item = dict(row)
            q = self._relation_quality_status(item, state=state)
            if q.get("relation_status") in {"suspect", "noisy"}:
                weak_rules.append(
                    {
                        "relation_id": item["id"],
                        "subject": item["subject_name"],
                        "relation_type": item["relation_type"],
                        "object": item["object_name"],
                        "confidence": item["confidence"],
                        "status": q.get("relation_status"),
                        "reasons": q.get("status_reasons", []),
                    }
                )
            desc = str(item.get("description", "") or "")
            if len(desc.strip()) <= 3 or any(tok in desc for tok in ("???", "TBD", "placeholder")):
                anomalous_desc.append(
                    {
                        "relation_id": item["id"],
                        "subject": item["subject_name"],
                        "relation_type": item["relation_type"],
                        "object": item["object_name"],
                        "description": desc,
                    }
                )
            if item.get("relation_type") == "related_to" and float(item.get("confidence", 0.0) or 0.0) < 0.45:
                merge_candidates.append(
                    {
                        "relation_id": item["id"],
                        "subject": item["subject_name"],
                        "object": item["object_name"],
                        "hint": "consider merge/refine to specific relation_type",
                    }
                )

        # mark candidates as archived_candidate in quality sidecar (no DB deletion)
        rel_state = state.setdefault("relations", {})
        for item in weak_rules[:limit]:
            rid = str(item.get("relation_id"))
            prev = rel_state.get(rid, {})
            prev.update(
                {
                    "relation_status": "archived_candidate",
                    "soft_isolated": True,
                    "reasons": list(dict.fromkeys(list(prev.get("reasons", [])) + list(item.get("reasons", [])))),
                }
            )
            rel_state[rid] = prev
        state.setdefault("cleanup_reports", []).append(str(report_file))
        state["cleanup_reports"] = state["cleanup_reports"][-50:]
        self._save_quality_state(state)

        payload = {
            "timestamp": self._utcnow(),
            "snapshot_file": str(snapshot_file),
            "report_file": str(report_file),
            "rules_trim_candidates": weak_rules[:limit],
            "merge_candidates": merge_candidates[:limit],
            "anomalous_description_candidates": anomalous_desc[:limit],
            "archived_candidate_marked": min(len(weak_rules), limit),
        }
        atomic_write_json(report_file, payload, ensure_ascii=False, indent=2)
        return {"status": "success", **payload}
