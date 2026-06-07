"""Unified MS8 engine (single runtime backed by bundled MemoryCore)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .record_policy import append_canonical_record

logger = logging.getLogger(__name__)


class MemoryCoreEngine:
    """Single engine implementation: MS8 directly runs bundled MemoryCore."""

    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self._core: Any | None = None
        self.available = False
        self._init_error: str | None = None
        self._records_file = runtime_dir / "memory" / "auto_memory_records.jsonl"
        self._quarantine_file = runtime_dir / "memory" / "noncanonical_quarantine.jsonl"
        self._governance_log = runtime_dir / "health" / "governance_fallback_log.jsonl"
        self._setup()

    def _setup(self) -> None:
        try:
            import os

            os.environ["OPENCLAW_MEMORY_WORKSPACE"] = str(self.runtime_dir)
            if "OPENCLAW_MEMORY_FAST_START" not in os.environ:
                os.environ["OPENCLAW_MEMORY_FAST_START"] = "1"

            from .engine_core.core import MemoryCore

            llm_enabled = False
            cfg_file = self.runtime_dir / "config.yaml"
            if cfg_file.exists():
                try:
                    payload = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        memory_cfg = payload.get("memory", {})
                        if isinstance(memory_cfg, dict):
                            llm_cfg = memory_cfg.get("llm", {})
                            if isinstance(llm_cfg, dict):
                                llm_enabled = bool(llm_cfg.get("enabled", False))
                except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
                    logger.debug("llm_config_read_failed err=%s", exc)

            self._core = MemoryCore(llm_enabled=llm_enabled)
            self.available = True

            if self._core is not None and hasattr(self._core, "config"):
                ws = self._core.config.get("workspace_dir")
                if ws:
                    self._records_file = Path(ws) / "memory" / "auto_memory_records.jsonl"
                    self._quarantine_file = Path(ws) / "memory" / "noncanonical_quarantine.jsonl"
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:  # pragma: no cover
            self.available = False
            self._init_error = str(exc)

    def status(self) -> dict[str, object]:
        return {
            "mode": "ms8_core",
            "available": self.available,
            "error": self._init_error,
            "records_file": str(self._records_file),
        }

    def records_file(self) -> Path:
        return self._records_file

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _fallback_error_code(reason: str) -> str:
        reason_key = str(reason or "").strip().lower()
        mapping = {
            "core_unavailable": "E_CORE_UNAVAILABLE",
            "core_write_disabled": "E_CORE_WRITE_DISABLED",
            "core_write_timeout_or_error": "E_CORE_WRITE_TIMEOUT",
            "core_retrieval_disabled": "E_CORE_RETRIEVAL_DISABLED",
            "core_retrieval_timeout_or_error": "E_CORE_RETRIEVAL_TIMEOUT",
            "core_retrieval_no_results_fallback": "E_CORE_RETRIEVAL_EMPTY",
        }
        return mapping.get(reason_key, "E_FALLBACK_GENERIC")

    def _fallback_write_record(self, text: str, source: str) -> dict[str, Any]:
        self._records_file.parent.mkdir(parents=True, exist_ok=True)
        row, ok, reason = append_canonical_record(
            records_file=self._records_file,
            quarantine_file=self._quarantine_file,
            text=str(text or ""),
            source=str(source or "unknown"),
            status="accepted",
        )
        if not ok:
            row.setdefault("meta", {})
            if isinstance(row["meta"], dict):
                row["meta"]["invalid_reason"] = reason
        return row

    @staticmethod
    def _content_hash(text: str) -> str:
        return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()

    def _find_existing_record(self, text: str, source: str, max_scan: int = 500) -> dict[str, Any] | None:
        if not self._records_file.exists():
            return None
        target_hash = self._content_hash(text)
        target_source = str(source or "")
        lines = self._records_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-max_scan:]
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.debug("skip_invalid_record_json err=%s", exc)
                continue
            if not isinstance(row, dict):
                continue
            row_text = str(row.get("normalized_text") or row.get("text") or "")
            row_source = str(row.get("source") or "")
            if row_source != target_source:
                continue
            if self._content_hash(row_text) == target_hash:
                return row
        return None

    @staticmethod
    def _query_targets_system_debug(query: str) -> bool:
        q = str(query or "").lower()
        debug_hints = (
            "ms8",
            "debug",
            "self-check",
            "self check",
            "maintenance",
            "governance",
            "policy",
            "review queue",
            "shadow",
            "压缩",
            "治理",
            "自检",
            "调试",
            "系统",
        )
        return any(k in q for k in debug_hints)

    @staticmethod
    def _is_expired(row: dict[str, Any]) -> bool:
        valid_until = str(row.get("valid_until") or row.get("ttl") or "").strip()
        if not valid_until:
            return False
        try:
            raw = valid_until[:-1] + "+00:00" if valid_until.endswith("Z") else valid_until
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc) < datetime.now(timezone.utc)
        except ValueError:
            return False

    def _policy_allows_recall(self, row: dict[str, Any], query: str = "") -> bool:
        status = str(row.get("status", "")).strip().lower()
        if status not in {
            "candidate",
            "short_term",
            "accepted",
            "verified",
            "pending_review",
            "quarantined",
            "stale",
            "superseded",
            "revoked",
        }:
            return False
        if row.get("can_recall", True) is False:
            return False
        # Keep candidate facts out of normal recall to reduce early-noise pollution.
        if status == "candidate":
            return False
        if str(row.get("superseded_by", "")).strip():
            return False
        if self._is_expired(row):
            return False
        # Recall lane: keep strict by default, avoid risky/unreviewed records.
        if status in {"pending_review", "quarantined", "stale", "superseded", "revoked"}:
            return False
        sensitivity = str(row.get("sensitivity", "private")).strip().lower()
        if sensitivity in {"secret", "credential"}:
            return False
        authority = str(row.get("authority", "user_implicit")).strip().lower()
        if authority in {"assistant_inferred", "tool_generated"} and status != "verified":
            return False
        scope = str(row.get("scope", "")).strip().lower()
        if scope == "system_debug" and not self._query_targets_system_debug(query):
            return False
        if scope == "labs":
            return False
        # Product decisions are useful but should inject less frequently:
        # only recall when query shows planning/decision intent.
        category = str(row.get("category", "")).strip().lower()
        if category == "product_decision":
            q = str(query or "").lower()
            decision_hints = (
                "方案",
                "策略",
                "决策",
                "优先级",
                "取舍",
                "发布",
                "路线",
                "plan",
                "decision",
                "tradeoff",
                "priority",
            )
            if not any(h in q for h in decision_hints):
                return False
        return True

    def _policy_allows_inject(self, row: dict[str, Any], query: str = "") -> bool:
        if not self._policy_allows_recall(row, query=query):
            return False
        if row.get("can_inject", True) is False:
            return False
        status = str(row.get("status", "")).strip().lower()
        if status not in {"accepted", "verified"}:
            return False
        return True

    def _filter_rows_by_policy(
        self,
        rows: list[dict[str, Any]],
        *,
        query: str,
        purpose: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        allowed: list[dict[str, Any]] = []
        blocked = 0
        use_inject = str(purpose or "").strip().lower() == "inject"
        for row in rows:
            ok = (
                self._policy_allows_inject(row, query=query)
                if use_inject
                else self._policy_allows_recall(row, query=query)
            )
            if ok:
                allowed.append(row)
            else:
                blocked += 1
        return allowed[:limit], {
            "purpose": "inject" if use_inject else "recall",
            "candidate_total": len(rows),
            "allowed_total": len(allowed),
            "blocked_total": blocked,
        }

    def _exact_fallback_matches(
        self,
        *,
        query: str,
        limit: int,
        purpose: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        q = str(query or "").strip().lower()
        fallback: list[dict[str, Any]] = []
        if q:
            for row in self.read_memories():
                txt = str(row.get("text") or row.get("normalized_text") or "")
                if q in txt.lower():
                    fallback.append(row)
        allowed, trace = self._filter_rows_by_policy(
            fallback,
            query=query,
            purpose=purpose,
            limit=max(limit, len(fallback)),
        )
        return allowed[:limit], trace

    @staticmethod
    def _merge_ranked_rows(
        exact_rows: list[dict[str, Any]],
        ranked_rows: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for row in [*exact_rows, *ranked_rows]:
            row_id = str(row.get("id") or "")
            row_text = str(row.get("text") or row.get("normalized_text") or "")
            key = row_id or row_text
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= limit:
                break
        return merged

    @staticmethod
    def _normalize_core_retrieval_row(row: dict[str, Any], index: int) -> dict[str, Any]:
        return {
            "id": str(row.get("id") or f"m-{index}"),
            "text": str(row.get("content") or row.get("text") or ""),
            "source": str(row.get("source", "memory_core")),
            "created_at": str(row.get("date") or row.get("created_at") or ""),
            "status": str(row.get("status") or "accepted"),
            "scope": str(row.get("scope") or ""),
            "category": str(row.get("category") or ""),
            "sensitivity": str(row.get("sensitivity") or "private"),
            "authority": str(row.get("authority") or "user_implicit"),
            "can_recall": row.get("can_recall", True),
            "can_inject": row.get("can_inject", True),
            "superseded_by": str(row.get("superseded_by") or ""),
            "valid_until": str(row.get("valid_until") or row.get("ttl") or ""),
            "score": row.get("score", row.get("scores", {}).get("fusion", 0.0) if isinstance(row.get("scores", {}), dict) else 0.0),
            "meta": {"engine": "ms8_core"},
        }

    def retrieve_gateway(
        self,
        query: str,
        *,
        limit: int = 50,
        purpose: str = "recall",
        allow_semantic: bool = False,
        allow_graph: bool = False,
    ) -> dict[str, Any]:
        purpose_name = "inject" if str(purpose or "").strip().lower() == "inject" else "recall"
        exact_rows, exact_trace = self._exact_fallback_matches(
            query=query,
            limit=limit,
            purpose=purpose_name,
        )
        trace: dict[str, Any] = {
            "purpose": purpose_name,
            "backend": "jsonl_fallback",
            "candidate_total": exact_trace.get("candidate_total", 0),
            "allowed_total": exact_trace.get("allowed_total", len(exact_rows)),
            "blocked_total": exact_trace.get("blocked_total", 0),
            "reason_codes": [],
            "policy_trace": dict(exact_trace),
            "ranking_trace": {
                "exact_match_count": len(exact_rows),
                "semantic_enabled": bool(allow_semantic),
                "graph_enabled": bool(allow_graph),
                "core_ranked_count": 0,
                "merge_strategy": "exact_first",
            },
            "context_budget": {"limit": int(max(1, limit))},
            "health_signals": {
                "engine_available": bool(self.available and self._core is not None),
                "core_error": self._init_error or "",
            },
        }

        if not self.available or self._core is None:
            trace["reason_codes"] = ["core_unavailable", "jsonl_exact_fallback"]
            return {"items": exact_rows[:limit], "trace": trace}

        if str(os.environ.get("MS8_USE_CORE_RETRIEVAL", "1")).strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            self._log_fallback("retrieve", "core_retrieval_disabled", {"query": query, "limit": limit})
            trace["reason_codes"] = ["core_retrieval_disabled", "jsonl_exact_fallback"]
            return {"items": exact_rows[:limit], "trace": trace}

        rows: list[dict[str, Any]] = []
        exc_holder: dict[str, Exception] = {}
        core = self._core
        if core is None:
            trace["reason_codes"] = ["core_unavailable", "jsonl_exact_fallback"]
            return {"items": exact_rows[:limit], "trace": trace}

        def runner() -> None:
            try:
                out = core.retrieve_memories(
                    query=query,
                    top_k=limit,
                    allow_semantic=allow_semantic,
                    allow_graph=allow_graph,
                )
                if isinstance(out, list):
                    rows.extend([x for x in out if isinstance(x, dict)])
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:  # pragma: no cover
                exc_holder["error"] = exc

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        t.join(timeout=8.0)
        if t.is_alive() or "error" in exc_holder:
            self._log_fallback(
                "retrieve",
                "core_retrieval_timeout_or_error",
                {"query": query, "limit": limit, "error": str(exc_holder.get("error", ""))},
            )
            trace["reason_codes"] = ["core_retrieval_timeout_or_error", "jsonl_exact_fallback"]
            return {"items": exact_rows[:limit], "trace": trace}

        normalized = [self._normalize_core_retrieval_row(row, i) for i, row in enumerate(rows)]
        allowed, policy_trace = self._filter_rows_by_policy(
            normalized,
            query=query,
            purpose=purpose_name,
            limit=limit,
        )
        trace.update(
            {
                "backend": "memory_core",
                "candidate_total": policy_trace.get("candidate_total", len(normalized)),
                "allowed_total": policy_trace.get("allowed_total", len(allowed)),
                "blocked_total": policy_trace.get("blocked_total", 0),
                "policy_trace": policy_trace,
            }
        )
        trace["ranking_trace"] = {
            "exact_match_count": len(exact_rows),
            "semantic_enabled": bool(allow_semantic),
            "graph_enabled": bool(allow_graph),
            "core_ranked_count": len(normalized),
            "merge_strategy": "exact_first",
        }
        if allowed:
            trace["reason_codes"] = ["policy_filtered", "core_ranked", "exact_first_merge"]
            merged = self._merge_ranked_rows(exact_rows, allowed, limit=limit)
            return {"items": merged, "trace": trace}

        self._log_fallback("retrieve", "core_retrieval_no_results_fallback", {"query": query, "limit": limit})
        trace["reason_codes"] = ["core_retrieval_no_results_fallback", "jsonl_exact_fallback"]
        return {"items": exact_rows[:limit], "trace": trace}

    def _log_fallback(self, kind: str, reason: str, extra: dict[str, Any] | None = None) -> None:
        try:
            self._governance_log.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "timestamp": self._utc_now(),
                "kind": str(kind or ""),
                "reason": str(reason or ""),
                "error_code": self._fallback_error_code(reason),
            }
            if isinstance(extra, dict):
                payload["extra"] = extra
            with self._governance_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("fallback_log_write_failed kind=%s reason=%s err=%s", kind, reason, exc)

    def write_memory(self, text: str, source: str = "ask") -> dict[str, Any]:
        if not self.available or self._core is None:
            row = self._fallback_write_record(text=text, source=source)
            self._log_fallback("write", "core_unavailable", {"source": source, "error": self._init_error or ""})
            return {
                "id": row["id"],
                "text": row["text"],
                "source": row["source"],
                "created_at": row["created_at"],
                "write_result": {
                    "record_id": row["id"],
                    "persisted": True,
                    "indexed": False,
                    "reviewed": False,
                    "fallback_used": True,
                    "reason": "core_unavailable",
                },
                "meta": {"engine": "ms8_core", "degraded": True},
            }
        if str(os.environ.get("MS8_USE_CORE_WRITE", "1")).strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            row = self._fallback_write_record(text=text, source=source)
            self._log_fallback("write", "core_write_disabled", {"source": source})
            return {
                "id": row["id"],
                "text": row["text"],
                "source": row["source"],
                "created_at": row["created_at"],
                "write_result": {
                    "record_id": row["id"],
                    "persisted": True,
                    "indexed": False,
                    "reviewed": False,
                    "fallback_used": True,
                    "reason": "core_write_disabled",
                },
                "meta": {"engine": "ms8_core"},
            }
        exc_holder: dict[str, Exception] = {}
        core = self._core
        if core is None:
            row = self._fallback_write_record(text=text, source=source)
            return {
                "id": row["id"],
                "text": row["text"],
                "source": row["source"],
                "created_at": row["created_at"],
                "write_result": {
                    "record_id": row["id"],
                    "persisted": True,
                    "indexed": False,
                    "reviewed": False,
                    "fallback_used": True,
                    "reason": "core_write_disabled",
                },
                "meta": {"engine": "ms8_core"},
            }

        def runner() -> None:
            try:
                if hasattr(core, "write_gateway"):
                    core.write_gateway(  # type: ignore[attr-defined]
                        text,
                        source=source,
                        category="general",
                        write_daily_log=True,
                    )
                else:
                    core.append_interaction(text)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:  # pragma: no cover
                exc_holder["error"] = exc

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        t.join(timeout=8.0)
        if t.is_alive() or "error" in exc_holder:
            self._log_fallback(
                "write",
                "core_write_timeout_or_error",
                {"source": source, "error": str(exc_holder.get("error", ""))},
            )
            row = self._fallback_write_record(text=text, source=source)
            return {
                "id": row["id"],
                "text": row["text"],
                "source": row["source"],
                "created_at": row["created_at"],
                "write_result": {
                    "record_id": row["id"],
                    "persisted": True,
                    "indexed": False,
                    "reviewed": False,
                    "fallback_used": True,
                    "reason": "core_write_timeout_or_error",
                },
                "meta": {"engine": "ms8_core"},
            }

        existing = self._find_existing_record(text=text, source=source)
        if existing is not None:
            rec_id = str(existing.get("id", ""))
            return {
                "id": rec_id,
                "text": str(existing.get("text") or existing.get("normalized_text") or text),
                "source": str(existing.get("source") or source),
                "created_at": str(existing.get("created_at") or self._utc_now()),
                "write_result": {
                    "record_id": rec_id,
                    "persisted": True,
                    "indexed": True,
                    "reviewed": False,
                    "fallback_used": False,
                    "reason": "core_already_persisted",
                },
                "meta": {"engine": "ms8_core"},
            }

        row = self._fallback_write_record(text=text, source=source)
        return {
            "id": row["id"],
            "text": row["text"],
            "source": row["source"],
            "created_at": row["created_at"],
            "write_result": {
                "record_id": row["id"],
                "persisted": True,
                "indexed": True,
                "reviewed": False,
                "fallback_used": True,
                "reason": "canonical_fallback_after_core_write",
            },
            "meta": {"engine": "ms8_core"},
        }

    def read_memories(self) -> list[dict[str, Any]]:
        if not self._records_file.exists():
            return []
        rows: list[dict[str, Any]] = []
        import json

        for line in self._records_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.debug("skip_invalid_memory_json err=%s", exc)
                continue
            if isinstance(obj, dict):
                if "text" not in obj and "normalized_text" in obj:
                    obj["text"] = obj.get("normalized_text", "")
                # Keep raw-readable rows; query-aware policy filtering is enforced in search path.
                if obj.get("can_recall", True) is False:
                    continue
                rows.append(obj)
        return rows

    def search_memories(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        result = self.retrieve_gateway(
            query=query,
            limit=limit,
            purpose="recall",
            allow_semantic=False,
            allow_graph=False,
        )
        return [row for row in result.get("items", []) if isinstance(row, dict)]

    def count_memories(self) -> int:
        return len(self.read_memories())

    def last_write_time(self) -> str | None:
        rows = self.read_memories()
        if not rows:
            return None
        last = rows[-1]
        ts = str(last.get("created_at", "")).strip()
        return ts or None

    def run_self_check(self, level: str = "L4") -> dict[str, Any]:
        if not self.available or self._core is None:
            return {"status": "error", "error": self._init_error or "unavailable", "results": []}
        return self._core.run_self_check(level=level)

    def get_monitoring_status(self) -> dict[str, Any]:
        if not self.available or self._core is None:
            return {"enabled": False}
        return self._core.get_monitoring_status()

    def shadow_status(self) -> dict[str, Any]:
        if not self.available or self._core is None:
            return {"status": "unavailable"}
        return self._core.shadow_status()

    def get_knowledge_graph_stats(self) -> dict[str, Any]:
        if not self.available or self._core is None:
            return {"entity_total": 0, "relation_total": 0}
        return self._core.get_knowledge_graph_stats()

    def get_response_memory_context(self, message: str, top_k: int = 5) -> dict[str, Any]:
        if not self.available or self._core is None:
            retrieval = self.retrieve_gateway(
                query=message,
                limit=max(1, top_k),
                purpose="inject",
                allow_semantic=False,
                allow_graph=False,
            )
            matches = [row for row in retrieval.get("items", []) if isinstance(row, dict)]
            trace = retrieval.get("trace", {}) if isinstance(retrieval.get("trace", {}), dict) else {}
            return {
                "context": "",
                "context_with_expression": "",
                "system_prompt_extra": "",
                "should_inject": bool(matches),
                "ranked": matches[: max(1, top_k)],
                "retrieval_gateway": trace,
                "expression_mode": {
                    "mode": "normal",
                    "confidence": 1.0,
                    "prompt_extra": "",
                    "decision": {"mode": "normal", "reason": "engine_fallback_normal"},
                },
            }
        if hasattr(self._core, "get_response_memory_context"):
            payload = self._core.get_response_memory_context(message, top_k=top_k)
            if isinstance(payload, dict):
                return payload
        return {
            "context": "",
            "context_with_expression": "",
            "system_prompt_extra": "",
            "should_inject": False,
            "ranked": [],
            "expression_mode": {
                "mode": "normal",
                "confidence": 1.0,
                "prompt_extra": "",
                "decision": {"mode": "normal", "reason": "core_context_unavailable"},
            },
        }

    def run_maintenance_now(self, force: bool = True) -> dict[str, Any]:
        if not self.available or self._core is None:
            return {"status": "error", "error": self._init_error or "unavailable"}
        return self._core.run_maintenance_now(force=force)


def get_engine(runtime_dir: Path) -> MemoryCoreEngine:
    return MemoryCoreEngine(runtime_dir)


def get_engine_status(runtime_dir: Path) -> dict[str, object]:
    return get_engine(runtime_dir).status()
