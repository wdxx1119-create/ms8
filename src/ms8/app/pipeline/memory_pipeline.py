from __future__ import annotations

import hashlib
import time
from pathlib import Path

from ms8.app.classifier.context_builder import ContextBuilder
from ms8.app.classifier.hybrid_classifier import HybridClassifier
from ms8.app.classifier.llm_classifier import LLMClassifier
from ms8.app.classifier.rule_classifier import RuleClassifier
from ms8.app.classifier.threshold_manager import ThresholdManager
from ms8.app.config import AutoMemoryConfig
from ms8.app.extractors.action_extractor import extract_action_object
from ms8.app.extractors.entity_extractor import extract_entities
from ms8.app.extractors.technical_extractor import extract_time_info
from ms8.app.feedback.feedback_service import FeedbackService
from ms8.app.feedback.rule_optimizer import RuleOptimizer
from ms8.app.integrations.ollama_client import OllamaClient
from ms8.app.memory.indexer import MemoryIndexer
from ms8.app.memory.repository import MemoryRepository
from ms8.app.observability.logger import PipelineLogger
from ms8.app.observability.metrics import MetricsCollector
from ms8.app.observability.trace import TraceCollector
from ms8.app.pipeline.consistency import consistency_check
from ms8.app.pipeline.decision import final_decision
from ms8.app.pipeline.dedupe import dedupe_check
from ms8.app.pipeline.memory_admission_engine import evaluate_candidate
from ms8.app.pipeline.quality_gate import quality_gate
from ms8.app.review.review_service import ReviewService
from ms8.app.rules.extraction_rules import extract_signals
from ms8.app.rules.preprocess_rules import preprocess_text
from ms8.app.rules.registry import RuleRegistry
from ms8.app.rules.tag_rules import derive_aux_tags
from ms8.app.schemas.pipeline_schema import MemoryRecord, PipelineResult
from ms8.app.schemas.review_schema import ReviewItem


class MemoryPipeline:
    def __init__(self, workspace_dir: Path, config: AutoMemoryConfig) -> None:
        self.workspace_dir = workspace_dir
        self.config = config

        store_path = workspace_dir / "memory" / "auto_memory_records.jsonl"
        index_path = workspace_dir / "memory" / "auto_memory_index.json"
        log_path = workspace_dir / "memory" / "auto_memory_pipeline.log"

        self.registry = RuleRegistry()
        self.threshold_manager = ThresholdManager(config.thresholds)
        self.repo = MemoryRepository(store_path)
        self.indexer = MemoryIndexer(
            index_path,
            hot_min_confidence=float(config.thresholds.index_hot_min_confidence),
            excluded_source_prefixes=list(config.exclude_source_prefixes_for_index),
        )
        self.logger = PipelineLogger(
            log_path,
            excluded_source_prefixes=list(config.exclude_source_prefixes_for_log),
            max_bytes=int(config.pipeline_log_max_bytes),
        )
        self.metrics = MetricsCollector()
        self.review_service = ReviewService(workspace_dir / "memory" / "auto_memory_review_queue.jsonl")
        self.feedback_service = FeedbackService(workspace_dir / "memory" / "auto_memory_feedback.jsonl")
        self.rule_optimizer = RuleOptimizer(
            self.feedback_service,
            self.threshold_manager,
            low_ratio=float(config.thresholds.feedback_low_ratio),
            high_ratio=float(config.thresholds.feedback_high_ratio),
            raise_step=float(config.thresholds.feedback_raise_step),
            drop_step=float(config.thresholds.feedback_drop_step),
            floor_threshold=float(config.thresholds.feedback_floor_threshold),
        )
        self.context_builder = ContextBuilder()

        rule_classifier = RuleClassifier(self.registry.category_rules_sorted())
        llm_classifier = LLMClassifier(
            OllamaClient(config.ollama.base_url, config.ollama.model, config.ollama.timeout_seconds),
            categories=config.allow_categories,
        )
        self.hybrid_classifier = HybridClassifier(
            rule_classifier,
            llm_classifier,
            self.threshold_manager,
            use_llm=bool(config.use_llm),
            context_dependency_min_confidence=float(config.thresholds.hybrid_context_dependency_min_confidence),
        )

    def record_feedback(
        self,
        memory_id: str,
        category: str,
        signal: str,
        helpful: bool,
        note: str = "",
        source: str = "user",
        confidence: float = 0.0,
    ) -> dict:
        from ms8.app.schemas.feedback_schema import FeedbackItem

        item = FeedbackItem(
            memory_id=memory_id,
            signal=signal,
            category=category,
            helpful=helpful,
            note=note,
            source=source,
            confidence=confidence,
        )
        self.feedback_service.add(item)
        return {"status": "success", "saved": item.__dict__}

    def weekly_threshold_suggestions(self) -> dict:
        out = self.workspace_dir / "memory" / "threshold_suggestions_weekly.json"
        return self.rule_optimizer.suggest_threshold_updates(lookback_days=7, min_samples=5, output_path=out)

    def review_pending(self) -> list:
        return [x.__dict__ for x in self.review_service.list_pending()]

    def _mk_id(self, text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

    def cleanup_test_pollution(self) -> dict:
        removed_repo = self.repo.cleanup(
            excluded_source_prefixes=list(self.config.exclude_source_prefixes_for_index),
            drop_rejected=True,
        )
        removed_index_source = self.indexer.cleanup_excluded()
        removed_index_rejected = self.indexer.cleanup_rejected()
        log_path = self.logger.log_path
        removed_log_lines = 0
        if log_path.exists():
            kept = []
            for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                low = line.lower()
                if "verify_canary" in low or '"source": "test' in low:
                    removed_log_lines += 1
                    continue
                kept.append(line)
            log_path.write_text(("\n".join(kept) + ("\n" if kept else "")), encoding="utf-8")
        return {
            "status": "success",
            "repository_cleanup": removed_repo,
            "index_cleanup_excluded_source": removed_index_source,
            "index_cleanup_rejected": removed_index_rejected,
            "pipeline_log_removed_lines": removed_log_lines,
        }

    def process(self, text: str, source: str = "interaction") -> PipelineResult:
        started = time.perf_counter()
        trace = TraceCollector()
        dropped: list[dict] = []
        records: list[MemoryRecord] = []

        # 1 preprocess
        normalized = preprocess_text(text)
        trace.add("preprocess", {"normalized_len": len(normalized)})

        # 2 quality_gate
        ok, quality_reason = quality_gate(normalized, self.config.quality_gate, self.config.thresholds)
        trace.add("quality_gate", {"ok": ok, "reason": quality_reason})
        if not ok:
            self.metrics.inc("dropped_quality")
            self.metrics.inc(f"dropped_quality_{quality_reason}")
            return PipelineResult(
                status="dropped",
                dropped=[{"stage": "quality_gate", "reason": quality_reason}],
                trace_id=trace.trace_id,
                metrics=self.metrics.snapshot(),
            )

        # 3 admission governance (block/privacy/worthiness/conflict hint)
        admission = evaluate_candidate(normalized, metadata={"source": source})
        normalized = admission.normalized_text
        trace.add("admission", admission.to_dict())
        if admission.route in {"rejected", "short_term_only"}:
            metric_key = "dropped_block" if admission.route == "rejected" else "dropped_short_term_only"
            self.metrics.inc(metric_key)
            return PipelineResult(
                status="dropped",
                dropped=[{"stage": "admission", "reason": admission.reasons, "route": admission.route}],
                trace_id=trace.trace_id,
                metrics=self.metrics.snapshot(),
            )

        # 5 dedupe (context load for subsequent steps)
        recent = self.repo.list_recent(limit=20)

        # 6 context_builder
        context = self.context_builder.build(normalized, latest_memories=recent)
        trace.add("context_builder", context)

        # 7 hybrid_classifier
        cls = self.hybrid_classifier.classify(normalized, context)
        category = cls["category"]
        confidence = float(cls["confidence"])
        tags = list(cls.get("tags", []))
        trace.add("hybrid_classifier", cls)

        if category not in self.config.allow_categories:
            self.metrics.inc("dropped_unknown_category")
            return PipelineResult(
                status="dropped",
                dropped=[{"stage": "hybrid_classifier", "reason": f"category_not_allowed:{category}"}],
                trace_id=trace.trace_id,
                metrics=self.metrics.snapshot(),
            )

        allow_write, dedupe_key, duplicate_of, dedupe_mode, recent_dup_count = dedupe_check(
            self.repo,
            category,
            normalized,
            cfg=self.config.dedupe,
        )
        trace.add(
            "dedupe",
            {
                "allow_write": allow_write,
                "dedupe_mode": dedupe_mode,
                "recent_dup_count": recent_dup_count,
                "duplicate_of": duplicate_of,
                "dedupe_key": dedupe_key,
            },
        )
        if not allow_write:
            self.metrics.inc("dropped_duplicate")
            self.metrics.inc(f"dropped_duplicate_{dedupe_mode}")
            dropped.append(
                {
                    "stage": "dedupe",
                    "reason": "duplicate",
                    "dedupe_mode": dedupe_mode,
                    "recent_dup_count": recent_dup_count,
                    "duplicate_of": duplicate_of,
                }
            )
            return PipelineResult(
                status="dropped",
                dropped=dropped,
                trace_id=trace.trace_id,
                metrics=self.metrics.snapshot(),
            )
        if dedupe_mode == "soft_duplicate":
            self.metrics.inc("soft_duplicate_saved")
        if dedupe_mode.startswith("soft_duplicate"):
            self.metrics.inc(f"{dedupe_mode}_saved")

        # 8 extraction
        signals = extract_signals(normalized)
        tags = sorted(set(tags + derive_aux_tags(category, signals)))
        if dedupe_mode.startswith("soft_duplicate"):
            tags = sorted(set(tags + ["duplicate"]))
        entities = extract_entities(normalized)
        action, obj, status = extract_action_object(normalized)
        time_info = extract_time_info(normalized)
        trace.add(
            "extraction",
            {
                "signals": signals,
                "entities": entities,
                "action": action,
                "object": obj,
                "status": status,
            },
        )

        # 9 consistency
        consistent, conflict_flag, conflict_reason = consistency_check(normalized)
        trace.add(
            "consistency",
            {"consistent": consistent, "conflict_flag": conflict_flag, "reason": conflict_reason},
        )

        # 10 decision
        save_status, needs_review, review_reason = final_decision(
            category,
            confidence,
            self.threshold_manager,
            self.config.review_confidence_threshold,
            conflict_flag,
        )
        if confidence < float(self.config.thresholds.low_confidence_review_min) and not needs_review:
            needs_review = True
            review_reason = "low_confidence_category_check"
        if admission.route == "pending_review":
            save_status = "pending_review"
            needs_review = True
            review_reason = (
                review_reason if review_reason and review_reason != "accepted" else "admission_pending_review"
            )
        elif admission.route == "redacted_accept":
            if save_status == "rejected":
                save_status = "pending_review"
                needs_review = True
            review_reason = (
                review_reason if review_reason and review_reason != "accepted" else "admission_redacted_accept"
            )
        trace.add(
            "decision",
            {"status": save_status, "needs_review": needs_review, "review_reason": review_reason},
        )

        llm_meta = cls.get("llm_meta", {}) if isinstance(cls.get("llm_meta", {}), dict) else {}
        record = MemoryRecord(
            text=normalized if admission.redacted else text,
            normalized_text=normalized,
            category=category,
            confidence=confidence,
            tags=tags,
            entities=entities,
            action=action,
            object=obj,
            status=save_status,
            time_info=time_info,
            matched_rules=list(cls.get("matched_rules", [])),
            llm_used=bool(cls.get("llm_used", False)),
            needs_review=needs_review,
            review_reason=review_reason,
            duplicate_of=duplicate_of or None,
            conflict_flag=conflict_flag,
            source=source,
            meta={
                "id": self._mk_id(normalized),
                "dedupe_key": dedupe_key,
                "dedupe_mode": dedupe_mode,
                "recent_dup_count": recent_dup_count,
                "signals": signals,
                "classifier_reason": cls.get("reason", ""),
                "llm_error": cls.get("llm_error", ""),
                "gray_reason": cls.get("gray_reason", ""),
                "llm_meta": llm_meta,
                "admission": admission.to_dict(),
            },
        )

        # 11 repository / 12 indexer incremental
        should_persist_main = admission.should_persist_main and (save_status in {"accepted", "pending_review"})
        saved = {
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
            "id": str(record.meta.get("id", "")),
            "meta": record.meta,
        }
        saved_meta_dict: dict[str, object] = {}
        if should_persist_main:
            saved = self.repo.save(record)
            saved_meta = saved.get("meta", {})
            saved_meta_dict = saved_meta if isinstance(saved_meta, dict) else {}
            trace.add("repository", {"saved": True, "id": saved_meta_dict.get("id")})
            if admission.should_index:
                self.indexer.add(saved)
                trace.add("indexer", {"indexed": True})
            else:
                trace.add("indexer", {"indexed": False, "reason": "admission_no_index"})
        else:
            trace.add("repository", {"saved": False, "reason": "rejected"})
            trace.add("indexer", {"indexed": False, "reason": "rejected"})
            self.metrics.inc("rejected_not_saved")

        # 13 logger
        duration_ms = int((time.perf_counter() - started) * 1000)
        trace_payload = trace.dump()
        trace_payload["duration_ms"] = duration_ms
        self.logger.log({"trace": trace_payload, "duration_ms": duration_ms, "memory": saved})
        trace.add("logger", {"logged": True})

        # 14 metrics
        if should_persist_main:
            self.metrics.inc("saved_total")
        else:
            self.metrics.inc("rejected_total")
        if record.llm_used:
            self.metrics.inc("llm_used")
        if needs_review:
            self.metrics.inc("needs_review")
            risk_level = (
                "high"
                if (conflict_flag or "privacy" in str(review_reason).lower())
                else ("medium" if confidence < 0.7 else "low")
            )
            effective_review_reason = str(review_reason or "").strip() or "unspecified_review"
            self.review_service.enqueue(
                ReviewItem(
                    memory_id=str(saved.get("id") or saved_meta_dict.get("id", "")),
                    reason=effective_review_reason,
                    review_reason=effective_review_reason,
                    confidence=confidence,
                    category=category,
                    risk_level=risk_level,
                )
            )

        records.append(record)
        return PipelineResult(
            status="success",
            records=records,
            dropped=dropped,
            metrics=self.metrics.snapshot(),
            trace_id=trace.trace_id,
        )
