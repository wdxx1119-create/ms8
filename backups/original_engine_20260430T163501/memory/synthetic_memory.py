"""Memory synthesis from knowledge graph patterns (quality-gated closed loop)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .knowledge_graph import RELATION_LABELS, RELATION_TYPES
from .file_write_guard import atomic_write_json, guarded_file_write


@dataclass
class SynthCandidate:
    candidate_id: str
    struct_key: str
    statement: str
    source_path: str
    source_type: str
    relation_type: str
    subject: str
    object: str
    scores: Dict[str, float]
    score_breakdown: Dict[str, float]
    confidence: float
    derivation_rule: str
    timestamp: str
    knowledge_tier: str
    trust_level: str
    admission_decision: str
    usage_permission: Dict[str, Any]
    promotion_state: str
    pattern_id: str
    pattern_type: str
    support_count: int
    supporting_instances: List[str]
    usage_count: int
    created_at: str
    status: str
    evidence: Dict[str, Any]


class MemorySynthesizer:
    """Generate, triage, review, and commit synthetic memories."""

    RELATION_ALIASES = {
        "?": "related_to",
        "unknown": "related_to",
        "相关": "related_to",
        "依赖": "depends_on",
        "组成": "part_of",
        "使用": "uses",
        "相似": "similar_to",
        "替代": "replaces",
        "创建": "creates",
        "归属": "belongs_to",
        "导致": "causes",
        "矛盾": "contradicts",
        "演变": "evolves_from",
        "学习": "learns_from",
    }

    def __init__(self, memory_core: Any) -> None:
        self.memory_core = memory_core
        self.arbitrator = getattr(memory_core, "knowledge_arbitrator", None)
        self.feedback = getattr(memory_core, "knowledge_feedback", None)
        self.settings = memory_core.config["settings"]["memory"].get("synthetic_memory", {})
        self.enabled = bool(self.settings.get("enabled", True))
        self.memory_dir = memory_core.config["memory_dir"]
        self.candidate_file = self.memory_dir / "synthetic_candidates.json"
        self.gap_file = self.memory_dir / "synthetic_gaps.json"
        self.history_file = self.memory_dir / "synthetic_history.json"
        self.pattern_file = self.memory_dir / "pattern_registry.json"

        self.accept_threshold = float(self.settings.get("accept_threshold", 0.82))
        self.review_threshold = float(self.settings.get("review_threshold", 0.68))
        self.special_reasoning_enabled = bool(self.settings.get("special_reasoning_enabled", True))
        self.reasoning_only_mode = bool(self.settings.get("reasoning_only_mode", False))
        self.use_llm = bool(self.settings.get("use_llm", False))
        self.auto_accept_enabled = bool(self.settings.get("auto_accept_enabled", True))
        self.auto_accept_limit = int(self.settings.get("auto_accept_limit", 8))
        self.rebuttal_reject_threshold = int(self.settings.get("max_rebuttal_before_reject", 2))
        self.promotion_min_hits = int(self.settings.get("promotion_min_hits", 2))

        self.candidate_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.candidate_file.exists():
            atomic_write_json(self.candidate_file, {"candidates": []}, ensure_ascii=False, indent=2)
        if not self.gap_file.exists():
            atomic_write_json(self.gap_file, {"gaps": []}, ensure_ascii=False, indent=2)
        if not self.history_file.exists():
            with guarded_file_write(self.history_file):
                self.history_file.write_text(
                    json.dumps({"accepted_struct_keys": [], "rejected_struct_keys": [], "last_updated_at": ""}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        if not self.pattern_file.exists():
            atomic_write_json(self.pattern_file, {"patterns": {}}, ensure_ascii=False, indent=2)

        self._migrate_legacy_statuses()
        if bool(self.settings.get("rebalance_on_start", True)):
            self.rebalance_review_queue(
                max_auto_accept=int(self.settings.get("rebalance_max_auto_accept", 40)),
                apply_writeback=bool(self.settings.get("rebalance_writeback", False)),
            )

    def _load_state(self) -> Dict[str, Any]:
        try:
            state = json.loads(self.candidate_file.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                return {"candidates": []}
            if "candidates" not in state or not isinstance(state.get("candidates"), list):
                state["candidates"] = []
            return state
        except Exception:
            return {"candidates": []}

    def _save_state(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.candidate_file, state, ensure_ascii=False, indent=2)

    def _dedupe_state_candidates(self, state: Dict[str, Any]) -> Dict[str, int]:
        candidates = state.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            return {"deduped": 0}
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in candidates:
            skey = str(item.get("struct_key", "")).strip()
            if not skey and item.get("subject") and item.get("object"):
                skey = self._struct_key(
                    str(item.get("subject", "")),
                    str(item.get("relation_type", "")),
                    str(item.get("object", "")),
                )
                item["struct_key"] = skey
            if not skey:
                skey = f"_no_key::{item.get('candidate_id', '')}"
            grouped.setdefault(skey, []).append(item)

        def _prio(it: Dict[str, Any]) -> tuple:
            status = str(it.get("status", "review"))
            status_rank = {"accepted": 3, "review": 2, "rejected": 1}.get(status, 0)
            score = float(it.get("scores", {}).get("total", 0.0) or 0.0)
            ts = str(it.get("accepted_at", it.get("created_at", "")))
            return (status_rank, score, ts)

        kept: List[Dict[str, Any]] = []
        archived = state.get("dedupe_archived", [])
        deduped = 0
        for skey, items in grouped.items():
            if len(items) == 1:
                kept.append(items[0])
                continue
            items.sort(key=_prio, reverse=True)
            winner = items[0]
            kept.append(winner)
            for dup in items[1:]:
                dup_copy = dict(dup)
                dup_copy["status"] = "rejected"
                dup_copy["review_note"] = f"dedupe_archived_duplicate_of:{winner.get('candidate_id')}"
                dup_copy["duplicate_of"] = winner.get("candidate_id")
                archived.append(dup_copy)
                deduped += 1
        state["candidates"] = kept
        state["dedupe_archived"] = archived[-2000:] if isinstance(archived, list) else []
        return {"deduped": deduped}

    def _load_history(self) -> Dict[str, Any]:
        try:
            obj = json.loads(self.history_file.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                return {"accepted_struct_keys": [], "rejected_struct_keys": [], "last_updated_at": ""}
            obj.setdefault("accepted_struct_keys", [])
            obj.setdefault("rejected_struct_keys", [])
            obj.setdefault("last_updated_at", "")
            return obj
        except Exception:
            return {"accepted_struct_keys": [], "rejected_struct_keys": [], "last_updated_at": ""}

    def _load_patterns(self) -> Dict[str, Any]:
        try:
            obj = json.loads(self.pattern_file.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                return {"patterns": {}}
            obj.setdefault("patterns", {})
            return obj
        except Exception:
            return {"patterns": {}}

    def _save_patterns(self, obj: Dict[str, Any]) -> None:
        obj["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.pattern_file, obj, ensure_ascii=False, indent=2)

    def _save_history(self, obj: Dict[str, Any]) -> None:
        obj["accepted_struct_keys"] = list(dict.fromkeys([str(x) for x in obj.get("accepted_struct_keys", [])]))[-5000:]
        obj["rejected_struct_keys"] = list(dict.fromkeys([str(x) for x in obj.get("rejected_struct_keys", [])]))[-5000:]
        obj["last_updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.history_file, obj, ensure_ascii=False, indent=2)

    def _migrate_legacy_statuses(self) -> None:
        state = self._load_state()
        changed = False
        for item in state.get("candidates", []):
            status = str(item.get("status", "")).strip().lower()
            if status in {"pending"} or (not self.reasoning_only_mode and status in {"candidate_reasoning_only", "promotion_ready"}):
                item["status"] = "review"
                changed = True
            # normalize missing score breakdown
            if "score_breakdown" not in item:
                scores = item.get("scores", {}) if isinstance(item.get("scores"), dict) else {}
                item["score_breakdown"] = {
                    "completeness": float(scores.get("consistency", 0.5)),
                    "consistency": float(scores.get("consistency", 0.5)),
                    "novelty": float(scores.get("novelty", 0.5)),
                    "usefulness": float(scores.get("usefulness", 0.5)),
                }
                changed = True
            if not item.get("struct_key") and item.get("subject") and item.get("object"):
                item["struct_key"] = self._struct_key(
                    str(item.get("subject", "")),
                    str(item.get("relation_type", "")),
                    str(item.get("object", "")),
                )
                changed = True
            if not item.get("candidate_id") and item.get("struct_key"):
                item["candidate_id"] = self._candidate_id(str(item.get("struct_key")))
                changed = True
            if "source_path" not in item:
                item["source_path"] = str((item.get("evidence") or {}).get("source_memory_ref", ""))
                changed = True
            if "source_type" not in item:
                item["source_type"] = "graph_relation"
                changed = True
            if "confidence" not in item:
                item["confidence"] = float((item.get("scores") or {}).get("confidence", 0.5))
                changed = True
            if "derivation_rule" not in item:
                depth = int((item.get("evidence") or {}).get("inference_depth", 1) or 1)
                item["derivation_rule"] = "two_hop_rule" if depth >= 2 else "graph_relation_rule"
                changed = True
            if "timestamp" not in item:
                item["timestamp"] = str(item.get("created_at", datetime.now(timezone.utc).isoformat()))
                changed = True
            if "knowledge_tier" not in item or "trust_level" not in item or "admission_decision" not in item or "usage_permission" not in item or "promotion_state" not in item:
                ctl = self._arbitrate_candidate(item)
                item["knowledge_tier"] = str(ctl.get("knowledge_tier", item.get("knowledge_tier", "observation")))
                item["trust_level"] = str(ctl.get("trust_level", item.get("trust_level", "hypothesis")))
                item["admission_decision"] = str(ctl.get("admission_decision", item.get("admission_decision", "hold")))
                item["usage_permission"] = dict(ctl.get("usage_permission", item.get("usage_permission", {"recall": True, "inject": "weak", "speak": "hint"})))
                item["promotion_state"] = str(ctl.get("promotion_state", item.get("promotion_state", "seeded")))
                changed = True
            if "pattern_id" not in item:
                item["pattern_id"] = ""
                changed = True
            if "pattern_type" not in item:
                item["pattern_type"] = ""
                changed = True
            if "support_count" not in item:
                item["support_count"] = 0
                changed = True
            if "supporting_instances" not in item or not isinstance(item.get("supporting_instances"), list):
                item["supporting_instances"] = []
                changed = True
            if "usage_count" not in item:
                item["usage_count"] = int(item.get("hit_count", 0) or 0)
                changed = True
        if changed:
            self._dedupe_state_candidates(state)
            self._save_state(state)

    def _normalize_text(self, value: str) -> str:
        value = str(value or "").strip().lower()
        value = re.sub(r"[`'\"“”‘’]+", "", value)
        value = re.sub(r"[\s_\-]+", " ", value)
        value = re.sub(r"[^\w\u4e00-\u9fff\s./:]", "", value)
        return value.strip()

    def _normalize_entity(self, value: str) -> str:
        v = self._normalize_text(value)
        alias_map = {
            "py": "python",
            "open claw": "openclaw",
            "llama 3.2": "llama3.2",
            "kg": "knowledge graph",
            "图谱": "知识图谱",
        }
        return alias_map.get(v, v)

    def _normalize_relation(self, relation_type: str) -> str:
        rt = self._normalize_text(relation_type)
        rt = self.RELATION_ALIASES.get(rt, rt)
        return rt if rt in RELATION_TYPES else "related_to"

    def _struct_key(self, subject: str, relation_type: str, object_name: str) -> str:
        return f"{self._normalize_entity(subject)}|{self._normalize_relation(relation_type)}|{self._normalize_entity(object_name)}"

    def _candidate_id(self, struct_key: str) -> str:
        return "cand_" + hashlib.sha1(struct_key.encode("utf-8")).hexdigest()[:16]

    def _relation_label(self, relation_type: str) -> str:
        return RELATION_LABELS.get(relation_type, relation_type)

    def _entity_importance(self, name: str) -> float:
        graph = self.memory_core.knowledge_graph
        if not graph:
            return 0.5
        matches = graph.search_entities(name, limit=1)
        if not matches:
            return 0.5
        return float(matches[0].get("importance", 0.5) or 0.5)

    def _score_candidate(self, relation: Dict[str, Any], struct_key: str, history_keys: set[str], existing_keys: set[str]) -> tuple[float, Dict[str, float], Dict[str, float]]:
        subject = str(relation.get("subject_name", relation.get("subject", "")))
        object_name = str(relation.get("object_name", relation.get("object", "")))
        rel_type = self._normalize_relation(str(relation.get("relation_type", relation.get("type", ""))))

        completeness = 1.0 if subject.strip() and object_name.strip() and rel_type in RELATION_TYPES else 0.0
        strength = float(relation.get("strength", 0.5) or 0.5)
        confidence = float(relation.get("confidence", 0.5) or 0.5)
        consistency = max(0.0, min(1.0, (strength + confidence) / 2.0))

        novelty = 1.0
        if struct_key in history_keys or struct_key in existing_keys:
            novelty = 0.05

        usefulness = (self._entity_importance(subject) + self._entity_importance(object_name)) / 2.0
        if rel_type in {"depends_on", "uses", "belongs_to", "creates"}:
            usefulness = min(1.0, usefulness + 0.08)
        if self.use_llm and 0.35 <= usefulness <= 0.9 and novelty > 0.05:
            statement = f"{subject} {self._relation_label(rel_type)} {object_name}"
            llm_boost = self._llm_usefulness_boost(statement)
            usefulness = max(0.0, min(1.0, usefulness + llm_boost))

        breakdown = {
            "completeness": round(completeness, 3),
            "consistency": round(consistency, 3),
            "novelty": round(novelty, 3),
            "usefulness": round(usefulness, 3),
        }
        total = round(
            0.30 * breakdown["completeness"]
            + 0.30 * breakdown["consistency"]
            + 0.20 * breakdown["novelty"]
            + 0.20 * breakdown["usefulness"],
            3,
        )
        scores = {
            "total": total,
            "confidence": round(confidence, 3),
            "strength": round(strength, 3),
        }
        return total, scores, breakdown

    def _llm_usefulness_boost(self, statement: str) -> float:
        """Return a tiny usefulness delta from local LLM, in [-0.06, +0.08]."""
        if not self.use_llm:
            return 0.0
        llm = getattr(getattr(self.memory_core, "self_improvement", None), "llm", None)
        if llm is None:
            return 0.0
        prompt = (
            "Score the practical usefulness of this relation for future assistant responses.\n"
            "Return ONLY one decimal number between 0 and 1.\n"
            f"relation: {statement}"
        )
        try:
            response = self.memory_core._run_async(
                llm.chat([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=12)
            )
            raw = str(response).strip()
            m = re.search(r"([01](?:\\.\\d+)?)", raw)
            if not m:
                return 0.0
            score = float(m.group(1))
            score = max(0.0, min(1.0, score))
            # Center at 0.6 to avoid over-inflating mediocre relations.
            return max(-0.06, min(0.08, (score - 0.6) * 0.2))
        except Exception:
            return 0.0

    def _triage_status(self, total_score: float, evidence: Dict[str, Any]) -> str:
        if self.reasoning_only_mode:
            if total_score >= self.review_threshold:
                return "candidate_reasoning_only"
            return "rejected"
        # Special-only reasoning status (not default path).
        if self.special_reasoning_enabled and int(evidence.get("inference_depth", 1)) >= 2 and bool(self.settings.get("force_two_hop_review", True)):
            return "review"
        if total_score >= self.accept_threshold:
            return "accepted"
        if total_score >= self.review_threshold:
            return "review"
        return "rejected"

    def _state_keys(self, state: Dict[str, Any]) -> set[str]:
        out: set[str] = set()
        for item in state.get("candidates", []):
            key = str(item.get("struct_key", "")).strip()
            if key:
                out.add(key)
                continue
            s = item.get("subject", "")
            r = item.get("relation_type", "")
            o = item.get("object", "")
            if s and o:
                out.add(self._struct_key(str(s), str(r), str(o)))
        return out

    def _append_to_memory_md(self, candidate: Dict[str, Any]) -> None:
        section = f"## Synthetic Accepted - {datetime.now(timezone.utc).date().isoformat()}"
        line = (
            f"- [{candidate.get('relation_type')}] "
            f"{candidate.get('subject')} {self._relation_label(str(candidate.get('relation_type')))} {candidate.get('object')} "
            f"(score={float(candidate.get('scores', {}).get('total', 0.0)):.2f}, id={candidate.get('candidate_id')})"
        )
        safe_line = line
        if hasattr(self.memory_core, "_safe_text_for_memory_md"):
            try:
                gate = self.memory_core._safe_text_for_memory_md(line)
                if not bool(gate.get("allowed", False)):
                    return
                safe_line = str(gate.get("text", line))
            except Exception:
                safe_line = line
        current = self.memory_core.file_store.read_memory_md()
        if safe_line in current:
            return
        if section not in current:
            updated = current.rstrip() + "\n\n" + section + "\n" + safe_line + "\n"
        else:
            updated = current.rstrip() + "\n" + safe_line + "\n"
        self.memory_core.file_store.write_memory_md(updated)

    def _write_back_to_graph(self, candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        graph = self.memory_core.knowledge_graph
        if not graph:
            return None
        return graph.add_relation(
            subject_name=str(candidate.get("subject", "")),
            relation_type=str(candidate.get("relation_type", "related_to")),
            object_name=str(candidate.get("object", "")),
            strength=float(candidate.get("scores", {}).get("strength", 0.5) or 0.5),
            description=str(candidate.get("statement", "")),
            confidence=float(candidate.get("scores", {}).get("confidence", 0.5) or 0.5),
            source_memory_ref=f"synthetic:{candidate.get('candidate_id')}",
        )

    def _route_candidate_write(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        tier = str(candidate.get("knowledge_tier", "observation"))
        decision = str(candidate.get("admission_decision", "hold"))
        out = {
            "graph_written": False,
            "core_written": False,
            "short_term_written": False,
            "writeback_mode": "unchanged",
            "strength_delta_sum": 0.0,
            "confidence_delta_sum": 0.0,
            "updated_relations_count": 0,
        }
        if decision != "admit":
            return out
        if tier in {"graph", "core"}:
            rel = self._write_back_to_graph(candidate)
            out["graph_written"] = bool(rel)
            if isinstance(rel, dict):
                out["writeback_mode"] = str(rel.get("writeback_mode", "unchanged"))
                out["strength_delta_sum"] = float(rel.get("strength_delta_sum", 0.0) or 0.0)
                out["confidence_delta_sum"] = float(rel.get("confidence_delta_sum", 0.0) or 0.0)
                out["updated_relations_count"] = int(rel.get("updated_relations_count", 0) or 0)
        if tier == "core":
            self._append_to_memory_md(candidate)
            out["core_written"] = True
        if tier in {"short_term", "observation"}:
            try:
                self.memory_core.working_memory.append_item(
                    content=str(candidate.get("statement", "")),
                    importance=float(candidate.get("scores", {}).get("total", 0.0) or 0.0),
                    topic=str(candidate.get("relation_type", "synthetic")),
                    source="synthetic",
                )
                out["short_term_written"] = True
            except Exception:
                pass
        return out

    def _arbitrate_candidate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        if not self.arbitrator:
            return {
                "knowledge_tier": "observation",
                "trust_level": "hypothesis",
                "admission_decision": "hold",
                "usage_permission": {"recall": True, "inject": "weak", "speak": "hint"},
                "promotion_state": "seeded",
            }
        return self.arbitrator.arbitrate_candidate(candidate)

    def _accept_candidate(self, item: Dict[str, Any], history: Dict[str, Any]) -> None:
        ctl = self._arbitrate_candidate(item)
        item.update(ctl)
        if str(item.get("admission_decision", "hold")) != "admit" or str(item.get("knowledge_tier", "observation")) == "rejected":
            self._reject_candidate(item, history, reason="arbitration_reject")
            return
        item["status"] = "accepted"
        item["accepted_at"] = datetime.now(timezone.utc).isoformat()
        item["review_note"] = "auto_accepted" if not item.get("review_note") else item.get("review_note")
        route = self._route_candidate_write(item)
        skey = str(item.get("struct_key", "")).strip()
        if skey:
            history.setdefault("accepted_struct_keys", []).append(skey)
        if self.feedback:
            self.feedback.record_admission(
                knowledge_id=str(item.get("candidate_id", "")),
                tier=str(item.get("knowledge_tier", "observation")),
                trust=str(item.get("trust_level", "hypothesis")),
                accepted_or_rejected="accepted",
                event="admission",
                extra={
                    "route": route,
                    "writeback_mode": route.get("writeback_mode", "unchanged"),
                    "strength_delta_sum": float(route.get("strength_delta_sum", 0.0) or 0.0),
                    "confidence_delta_sum": float(route.get("confidence_delta_sum", 0.0) or 0.0),
                    "updated_relations_count": int(route.get("updated_relations_count", 0) or 0),
                    "promotion_events": 1,
                },
            )

    def _reject_candidate(self, item: Dict[str, Any], history: Dict[str, Any], reason: str = "low_score") -> None:
        item["status"] = "rejected"
        item["rejected_at"] = datetime.now(timezone.utc).isoformat()
        item["review_note"] = reason
        item["knowledge_tier"] = "rejected"
        item["trust_level"] = "isolated"
        item["admission_decision"] = "reject"
        item["usage_permission"] = {"recall": False, "inject": "none", "speak": "deny"}
        item["promotion_state"] = "demoted"
        skey = str(item.get("struct_key", "")).strip()
        if skey:
            history.setdefault("rejected_struct_keys", []).append(skey)
        if self.feedback:
            self.feedback.record_admission(
                knowledge_id=str(item.get("candidate_id", "")),
                tier="rejected",
                trust="isolated",
                accepted_or_rejected="rejected",
                event="admission",
                extra={"demotion_events": 1, "reason": reason},
            )

    def rebalance_review_queue(self, max_auto_accept: int = 40, apply_writeback: bool = False) -> Dict[str, int]:
        state = self._load_state()
        history = self._load_history()
        accepted = 0
        rejected = 0
        kept_review = 0
        review_items: List[Dict[str, Any]] = []

        for item in state.get("candidates", []):
            if item.get("status") != "review":
                continue
            total = float(item.get("scores", {}).get("total", 0.0) or 0.0)
            if total >= self.accept_threshold and accepted < max(0, max_auto_accept):
                if apply_writeback:
                    self._accept_candidate(item, history)
                else:
                    item["status"] = "accepted"
                    item["accepted_at"] = datetime.now(timezone.utc).isoformat()
                    skey = str(item.get("struct_key", "")).strip()
                    if skey:
                        history.setdefault("accepted_struct_keys", []).append(skey)
                accepted += 1
            elif total < self.review_threshold:
                self._reject_candidate(item, history, reason="rebalance_low_score")
                rejected += 1
            else:
                kept_review += 1
                review_items.append(item)

        # Keep review pool bounded to avoid long-term backlog.
        target_review = int(self.settings.get("review_queue_target", 10))
        if target_review >= 0 and len(review_items) > target_review:
            review_items.sort(key=lambda x: float(x.get("scores", {}).get("total", 0.0)), reverse=True)
            for stale in review_items[target_review:]:
                self._reject_candidate(stale, history, reason="rebalance_backlog_trim")
                rejected += 1
                kept_review -= 1

        self._save_state(state)
        self._save_history(history)
        if apply_writeback and accepted > 0:
            try:
                self.memory_core.reindex_memory()
            except Exception:
                pass
        # Keep active pool deduped after rebalancing.
        self._dedupe_state_candidates(state)
        self._save_state(state)
        return {"accepted": accepted, "rejected": rejected, "review": kept_review}

    def _two_hop_relations(self, base_relations: List[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
        if not bool(self.settings.get("two_hop_enabled", True)):
            return []

        # Only safe relation combinations.
        allowed_pairs = {
            ("depends_on", "depends_on"): "depends_on",
            ("uses", "depends_on"): "depends_on",
            ("belongs_to", "part_of"): "belongs_to",
            ("uses", "uses"): "related_to",
        }

        by_subject: Dict[str, List[Dict[str, Any]]] = {}
        for r in base_relations:
            by_subject.setdefault(str(r.get("subject_name", "")), []).append(r)

        inferred: List[Dict[str, Any]] = []
        seen = set()
        for r1 in base_relations:
            a = str(r1.get("subject_name", ""))
            b = str(r1.get("object_name", ""))
            t1 = self._normalize_relation(str(r1.get("relation_type", "")))
            if not a or not b:
                continue
            for r2 in by_subject.get(b, []):
                c = str(r2.get("object_name", ""))
                t2 = self._normalize_relation(str(r2.get("relation_type", "")))
                if not c or c == a:
                    continue
                inferred_type = allowed_pairs.get((t1, t2))
                if not inferred_type:
                    continue
                key = (self._normalize_entity(a), inferred_type, self._normalize_entity(c))
                if key in seen:
                    continue
                seen.add(key)
                inferred.append(
                    {
                        "subject_name": a,
                        "object_name": c,
                        "relation_type": inferred_type,
                        "strength": round(min(float(r1.get("strength", 0.6)), float(r2.get("strength", 0.6))) * 0.85, 3),
                        "confidence": round(min(float(r1.get("confidence", 0.6)), float(r2.get("confidence", 0.6))) * 0.9, 3),
                        "id": None,
                        "description": f"2-hop inferred from {a}->{b} and {b}->{c}",
                        "inference_depth": 2,
                        "via_relation_ids": [r1.get("id"), r2.get("id")],
                    }
                )
                if len(inferred) >= limit:
                    return inferred
        return inferred

    def _detect_pattern_route(self, relation: Dict[str, Any], base_relations: List[Dict[str, Any]], pattern_state: Dict[str, Any]) -> Dict[str, Any]:
        rel_type = self._normalize_relation(str(relation.get("relation_type", "")))
        subject = str(relation.get("subject_name", relation.get("subject", "")))
        object_name = str(relation.get("object_name", relation.get("object", "")))
        desc = str(relation.get("description", "") or "")
        depth = int(relation.get("inference_depth", 1) or 1)

        pattern_type = ""
        if rel_type == "depends_on" and depth >= 2:
            pattern_type = "depends_on_chain"
        elif any(k in f"{subject} {object_name} {desc}".lower() for k in ("config", "配置", "setting")) and any(k in f"{subject} {object_name} {desc}".lower() for k in ("decision", "决定", "方案")):
            pattern_type = "config_change_decision"
        elif rel_type == "uses":
            freq = 0
            sub_key = self._normalize_entity(subject)
            obj_key = self._normalize_entity(object_name)
            for r in base_relations:
                s = self._normalize_entity(str(r.get("subject_name", "")))
                o = self._normalize_entity(str(r.get("object_name", "")))
                if s == sub_key or o == obj_key:
                    freq += 1
            if freq >= 3:
                pattern_type = "tool_usage_high_frequency"

        if not pattern_type:
            return {}

        pid = f"ptn::{pattern_type}::{self._normalize_entity(subject)}::{self._normalize_entity(object_name)}"
        reg = pattern_state.setdefault("patterns", {})
        item = reg.get(pid, {"pattern_id": pid, "pattern_type": pattern_type, "support_count": 0, "supporting_instances": [], "usage_count": 0, "promotion_state": "seeded"})
        item["support_count"] = int(item.get("support_count", 0) or 0) + 1
        relid = relation.get("id")
        if relid is not None:
            inst = f"rel:{relid}"
            if inst not in item.get("supporting_instances", []):
                item.setdefault("supporting_instances", []).append(inst)
                item["supporting_instances"] = item["supporting_instances"][-20:]
        # pre-reserve promotion state only, no full auto promotion
        if item["support_count"] >= int(self.settings.get("pattern_promotion_support_min", 5)):
            item["promotion_state"] = "watch"
        reg[pid] = item
        return item

    def generate_candidates(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.enabled or not self.memory_core.knowledge_graph:
            return []

        min_strength = float(self.settings.get("min_relation_strength", 0.5))
        max_candidates = int(self.settings.get("max_candidates", limit))
        allowed_relations = set(self.settings.get("allowed_relations", []))

        state = self._load_state()
        history = self._load_history()
        pattern_state = self._load_patterns()
        history_keys = set(str(x) for x in history.get("accepted_struct_keys", [])) | set(str(x) for x in history.get("rejected_struct_keys", []))
        existing_keys = self._state_keys(state)
        existing_pattern_ids = {str(x.get("pattern_id", "")) for x in state.get("candidates", []) if str(x.get("pattern_id", "")).strip()}

        relations = self.memory_core.knowledge_graph.list_relations(limit=max(40, limit * 6))
        base_relations = [
            r
            for r in relations
            if float(r.get("strength", 0.0) or 0.0) >= min_strength
            and str(r.get("relation_status", "stable")) not in {"suspect", "noisy"}
            and not bool(r.get("soft_isolated", False))
        ]
        pattern_relations = [
            r
            for r in relations
            if float(r.get("strength", 0.0) or 0.0) >= min_strength
            and str(r.get("relation_status", "stable")) not in {"suspect", "noisy"}
        ]
        # Prioritize inferred relations first so pattern routes (e.g. depends_on_chain)
        # are not starved by a large base relation pool.
        candidate_relations = self._two_hop_relations(base_relations, limit=max(10, limit))
        candidate_relations.extend(pattern_relations)

        added: List[Dict[str, Any]] = []
        auto_accepted = 0

        for relation in candidate_relations:
            relation_type = self._normalize_relation(str(relation.get("relation_type", relation.get("type", ""))))
            subject = str(relation.get("subject_name", relation.get("subject", ""))).strip()
            object_name = str(relation.get("object_name", relation.get("object", ""))).strip()
            if not subject or not object_name:
                continue
            struct_key = self._struct_key(subject, relation_type, object_name)

            total, scores, breakdown = self._score_candidate(relation, struct_key, history_keys, existing_keys)
            evidence = {
                "relation_id": relation.get("id"),
                "source_memory_ref": relation.get("source_memory_ref"),
                "inference_depth": int(relation.get("inference_depth", 1)),
                "via_relation_ids": relation.get("via_relation_ids", []),
            }
            status = self._triage_status(total, evidence)
            statement = f"{subject} {self._relation_label(relation_type)} {object_name}"
            source_ref = str(relation.get("source_memory_ref", "") or "")
            source_type = "graph_relation" if source_ref else "graph"
            ts = datetime.now(timezone.utc).isoformat()
            pattern_meta = self._detect_pattern_route(relation, base_relations, pattern_state)
            if pattern_meta:
                source_type = "pattern"
            elif allowed_relations and relation_type not in allowed_relations:
                # Keep existing relation whitelist behavior for non-pattern routes.
                continue
            if str(relation.get("relation_status", "stable")) == "archived_candidate" and not pattern_meta:
                # archived relations do not drive normal promotion candidates
                continue
            dedupe_key = struct_key
            if pattern_meta:
                dedupe_key = f"pattern::{pattern_meta.get('pattern_id', '')}"
                if dedupe_key in existing_keys or str(pattern_meta.get("pattern_id", "")) in existing_pattern_ids:
                    continue
            else:
                if struct_key in existing_keys or struct_key in history_keys:
                    continue

            base_candidate = {
                "candidate_id": self._candidate_id(struct_key),
                "struct_key": struct_key,
                "statement": statement,
                "source_path": source_ref,
                "source_type": source_type,
                "relation_type": relation_type,
                "subject": subject,
                "object": object_name,
                "scores": scores,
                "score_breakdown": breakdown,
                "confidence": float(scores.get("confidence", 0.5)),
                "derivation_rule": "two_hop_rule" if int(evidence.get("inference_depth", 1)) >= 2 else "graph_relation_rule",
                "timestamp": ts,
                "evidence": evidence,
                "pattern_id": str(pattern_meta.get("pattern_id", "")),
                "pattern_type": str(pattern_meta.get("pattern_type", "")),
                "support_count": int(pattern_meta.get("support_count", 0) or 0),
                "supporting_instances": list(pattern_meta.get("supporting_instances", [])),
                "usage_count": int(pattern_meta.get("usage_count", 0) or 0),
                "pattern_promotion_state": str(pattern_meta.get("promotion_state", "seeded")),
            }
            ctl = self._arbitrate_candidate(base_candidate)
            if str(ctl.get("admission_decision", "hold")) == "reject" or str(ctl.get("knowledge_tier", "observation")) == "rejected":
                status = "rejected"
            elif status == "accepted" and str(ctl.get("admission_decision", "hold")) != "admit":
                status = "review"

            candidate = SynthCandidate(
                candidate_id=self._candidate_id(struct_key),
                struct_key=dedupe_key,
                statement=statement,
                source_path=source_ref,
                source_type=source_type,
                relation_type=relation_type,
                subject=subject,
                object=object_name,
                scores=scores,
                score_breakdown=breakdown,
                confidence=float(scores.get("confidence", 0.5)),
                derivation_rule="two_hop_rule" if int(evidence.get("inference_depth", 1)) >= 2 else "graph_relation_rule",
                timestamp=ts,
                knowledge_tier=str(ctl.get("knowledge_tier", "observation")),
                trust_level=str(ctl.get("trust_level", "hypothesis")),
                admission_decision=str(ctl.get("admission_decision", "hold")),
                usage_permission=dict(ctl.get("usage_permission", {"recall": True, "inject": "weak", "speak": "hint"})),
                promotion_state=str(pattern_meta.get("promotion_state", ctl.get("promotion_state", "seeded"))),
                pattern_id=str(pattern_meta.get("pattern_id", "")),
                pattern_type=str(pattern_meta.get("pattern_type", "")),
                support_count=int(pattern_meta.get("support_count", 0) or 0),
                supporting_instances=list(pattern_meta.get("supporting_instances", [])),
                usage_count=int(pattern_meta.get("usage_count", 0) or 0),
                created_at=datetime.now(timezone.utc).isoformat(),
                status=status,
                evidence=evidence,
            ).__dict__
            if self.feedback:
                self.feedback.record_admission(
                    knowledge_id=str(candidate.get("candidate_id", "")),
                    tier=str(candidate.get("knowledge_tier", "observation")),
                    trust=str(candidate.get("trust_level", "hypothesis")),
                    accepted_or_rejected="pending",
                    event="candidate_generated",
                    extra={
                        "retrieval_hits": 0,
                        "retrieval_waste": 0,
                        "used_in_answer": False,
                    },
                )

            # Auto triage actions
            if status == "accepted" and self.auto_accept_enabled and auto_accepted < self.auto_accept_limit:
                self._accept_candidate(candidate, history)
                auto_accepted += 1
            elif status == "rejected":
                self._reject_candidate(candidate, history, reason="auto_rejected_low_score")

            state["candidates"].append(candidate)
            existing_keys.add(dedupe_key)
            if pattern_meta:
                existing_pattern_ids.add(str(pattern_meta.get("pattern_id", "")))
            added.append(candidate)
            if len(added) >= max_candidates:
                break

        self._dedupe_state_candidates(state)
        self._save_state(state)
        self._save_history(history)
        self._save_patterns(pattern_state)

        if any(item.get("status") == "accepted" for item in added):
            try:
                self.memory_core.reindex_memory()
            except Exception:
                pass

        return added

    def list_candidates(self, status: str = "review", limit: int = 20) -> List[Dict[str, Any]]:
        state = self._load_state()
        items = state.get("candidates", [])
        if status != "all":
            target = status
            if self.reasoning_only_mode:
                # Compatibility aliases for reasoning-only flow.
                if status == "review":
                    target = "candidate_reasoning_only"
            items = [item for item in items if str(item.get("status", "")) == target]
        items.sort(key=lambda x: float(x.get("scores", {}).get("total", 0.0)), reverse=True)
        return items[:limit]

    def list_reasoning_candidates(self, limit: int = 20) -> List[Dict[str, Any]]:
        # Compatibility method used by context injection with permission controls.
        state = self._load_state()
        review_items = [x for x in state.get("candidates", []) if x.get("status") == "review"]
        review_items.sort(key=lambda x: float(x.get("scores", {}).get("total", 0.0)), reverse=True)
        accepted_recent = [x for x in state.get("candidates", []) if x.get("status") == "accepted"]
        accepted_recent.sort(key=lambda x: str(x.get("accepted_at", "")), reverse=True)
        pool = review_items + accepted_recent
        # recall control
        pool = [x for x in pool if bool((x.get("usage_permission") or {}).get("recall", True))]
        # injection/speaking control
        pool = [x for x in pool if str((x.get("usage_permission") or {}).get("inject", "none")) != "none"]
        speak_rank = {"primary": 3, "support": 2, "hint": 1, "deny": 0}
        pool.sort(
            key=lambda x: (
                speak_rank.get(str((x.get("usage_permission") or {}).get("speak", "hint")), 1),
                float(x.get("scores", {}).get("total", 0.0)),
            ),
            reverse=True,
        )
        return pool[:limit]

    def record_candidate_hits(self, candidate_ids: List[str], used: bool = True, rebuttal: bool = False) -> Dict[str, Any]:
        state = self._load_state()
        history = self._load_history()
        pattern_state = self._load_patterns()
        updated = 0
        promoted = 0
        rejected = 0

        for item in state.get("candidates", []):
            cid = str(item.get("candidate_id", ""))
            if cid not in candidate_ids:
                continue
            if item.get("status") not in {"review", "accepted", "candidate_reasoning_only", "promotion_ready"}:
                continue
            if used:
                item["hit_count"] = int(item.get("hit_count", 0) or 0) + 1
                item["last_hit_at"] = datetime.now(timezone.utc).isoformat()
                item["usage_count"] = int(item.get("usage_count", 0) or 0) + 1
                pid = str(item.get("pattern_id", ""))
                if pid:
                    p = (pattern_state.setdefault("patterns", {})).setdefault(
                        pid,
                        {
                            "pattern_id": pid,
                            "pattern_type": str(item.get("pattern_type", "")),
                            "support_count": int(item.get("support_count", 0) or 0),
                            "supporting_instances": list(item.get("supporting_instances", [])),
                            "usage_count": 0,
                            "promotion_state": str(item.get("promotion_state", "seeded")),
                        },
                    )
                    p["usage_count"] = int(p.get("usage_count", 0) or 0) + 1
            if rebuttal:
                item["rebuttal_count"] = int(item.get("rebuttal_count", 0) or 0) + 1
            if int(item.get("rebuttal_count", 0) or 0) >= self.rebuttal_reject_threshold and item.get("status") != "accepted":
                self._reject_candidate(item, history, reason="rebuttal_threshold")
                rejected += 1
            elif item.get("status") in {"review", "candidate_reasoning_only"} and int(item.get("hit_count", 0) or 0) >= self.promotion_min_hits:
                if self.reasoning_only_mode:
                    item["status"] = "promotion_ready"
                    item["promotion_ready_at"] = datetime.now(timezone.utc).isoformat()
                    promoted += 1
                elif float(item.get("scores", {}).get("total", 0.0) or 0.0) >= max(self.review_threshold, self.accept_threshold - 0.03):
                    self._accept_candidate(item, history)
                    promoted += 1
            if self.feedback:
                self.feedback.record_usage(
                    knowledge_id=cid,
                    tier=str(item.get("knowledge_tier", "observation")),
                    trust=str(item.get("trust_level", "hypothesis")),
                    retrieval_hits=1 if used else 0,
                    retrieval_waste=0 if used else 1,
                    used_in_answer=bool(used),
                    extra={"channel": "synthetic_context", "promotion_events": 1 if promoted > 0 else 0},
                )
            updated += 1

        self._dedupe_state_candidates(state)
        self._save_state(state)
        self._save_history(history)
        self._save_patterns(pattern_state)
        if promoted > 0:
            try:
                self.memory_core.reindex_memory()
            except Exception:
                pass
        return {"updated": updated, "promotion_ready": promoted, "rejected": rejected}

    def review_candidates(self, decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
        state = self._load_state()
        history = self._load_history()
        accepted = 0
        rejected = 0
        updated = 0

        by_id = {str(x.get("candidate_id", "")): x for x in state.get("candidates", [])}
        for decision in decisions:
            cid = str(decision.get("candidate_id", ""))
            action = str(decision.get("decision", "")).strip().lower()
            note = str(decision.get("note", "")).strip()
            item = by_id.get(cid)
            if not item:
                continue
            if item.get("status") in {"accepted", "rejected"}:
                continue

            if action == "accept":
                item["review_note"] = note or "manual_accept"
                self._accept_candidate(item, history)
                accepted += 1
                updated += 1
            elif action == "reject":
                self._reject_candidate(item, history, reason=note or "manual_reject")
                rejected += 1
                updated += 1

        self._dedupe_state_candidates(state)
        self._save_state(state)
        self._save_history(history)
        if accepted > 0:
            try:
                self.memory_core.reindex_memory()
            except Exception:
                pass
        return {"updated": updated, "accepted": accepted, "rejected": rejected}

    def confirm_candidates(self, candidate_ids: Optional[List[str]] = None, min_score: Optional[float] = None) -> Dict[str, Any]:
        state = self._load_state()
        history = self._load_history()
        accepted_rows: List[Dict[str, Any]] = []
        min_score = float(min_score) if min_score is not None else self.accept_threshold

        for item in state.get("candidates", []):
            if item.get("status") != "review":
                continue
            cid = str(item.get("candidate_id", ""))
            if candidate_ids and cid not in candidate_ids:
                continue
            score = float(item.get("scores", {}).get("total", 0.0) or 0.0)
            if score < min_score:
                continue
            self._accept_candidate(item, history)
            accepted_rows.append(item)

        self._save_state(state)
        self._save_history(history)
        if accepted_rows:
            try:
                self.memory_core.reindex_memory()
            except Exception:
                pass
        return {"accepted": accepted_rows, "rejected": []}

    def reject_candidates(self, candidate_ids: List[str]) -> Dict[str, Any]:
        state = self._load_state()
        history = self._load_history()
        rejected: List[str] = []

        for item in state.get("candidates", []):
            cid = str(item.get("candidate_id", ""))
            if cid in candidate_ids and item.get("status") == "review":
                self._reject_candidate(item, history, reason="manual_bulk_reject")
                rejected.append(cid)

        self._dedupe_state_candidates(state)
        self._save_state(state)
        self._save_history(history)
        return {"rejected": rejected}

    def discover_gaps(self, limit: int = 10) -> Dict[str, Any]:
        if not self.memory_core.knowledge_graph:
            return {"gaps": []}
        gap_cfg = self.settings.get("gap_detection", {})
        min_importance = float(gap_cfg.get("min_importance", 0.6))
        max_relations = int(gap_cfg.get("max_relations", 1))
        gaps = self.memory_core.knowledge_graph.gap_report(
            min_importance=min_importance,
            max_relations=max_relations,
            limit=limit,
        )
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "gaps": gaps}
        atomic_write_json(self.gap_file, payload, ensure_ascii=False, indent=2)
        return payload

    def health_report(self) -> Dict[str, Any]:
        state = self._load_state()
        candidates = state.get("candidates", [])
        total = len(candidates)
        if total == 0:
            return {
                "total_candidates": 0,
                "duplicate_rate": 0.0,
                "isolated_node_ratio": 0.0,
                "low_quality_relation_ratio": 0.0,
                "accepted_growth_rate_7d": 0.0,
            }

        # Duplicate rate by struct_key repetition inside candidate pool.
        keys = [str(c.get("struct_key", "")) for c in candidates if c.get("struct_key")]
        unique_keys = len(set(keys)) if keys else 0
        duplicate_rate = round(max(0.0, (len(keys) - unique_keys) / max(1, len(keys))), 4)

        # Graph quality indicators.
        kg = self.memory_core.knowledge_graph
        isolated_ratio = 0.0
        low_quality_ratio = 0.0
        if kg:
            try:
                stats = kg.stats()
                entities_total = int(stats.get("entity_total", stats.get("entities_total", 0)) or 0)
                gaps = kg.gap_report(min_importance=0.2, max_relations=0, limit=max(entities_total, 1))
                isolated_ratio = round(len(gaps) / max(1, entities_total), 4) if entities_total > 0 else 0.0

                rels = kg.list_relations(limit=5000)
                if rels:
                    low_quality = sum(1 for r in rels if float(r.get("confidence", 0.0) or 0.0) < 0.45)
                    low_quality_ratio = round(low_quality / len(rels), 4)
            except Exception:
                pass

        now = datetime.now(timezone.utc)
        accepted_recent = 0
        accepted_total = 0
        for c in candidates:
            if c.get("status") != "accepted":
                continue
            accepted_total += 1
            ts = str(c.get("accepted_at", ""))
            try:
                raw = ts.strip()
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
            except Exception:
                continue
            if now - dt <= timedelta(days=7):
                accepted_recent += 1
        accepted_growth = round(accepted_recent / max(1, accepted_total), 4)

        return {
            "total_candidates": total,
            "status_distribution": {
                "accepted": sum(1 for c in candidates if c.get("status") == "accepted"),
                "review": sum(1 for c in candidates if c.get("status") == "review"),
                "rejected": sum(1 for c in candidates if c.get("status") == "rejected"),
                "reasoning_only": sum(1 for c in candidates if c.get("status") == "reasoning_only"),
            },
            "duplicate_rate": duplicate_rate,
            "isolated_node_ratio": isolated_ratio,
            "low_quality_relation_ratio": low_quality_ratio,
            "accepted_growth_rate_7d": accepted_growth,
            "accept_threshold": self.accept_threshold,
            "review_threshold": self.review_threshold,
            "use_llm": bool(self.use_llm),
        }
