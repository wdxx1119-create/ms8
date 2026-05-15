from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List

from app.classifier.context_builder import ContextBuilder
from app.classifier.hybrid_classifier import HybridClassifier
from app.classifier.llm_classifier import LLMClassifier
from app.classifier.rule_classifier import RuleClassifier
from app.classifier.threshold_manager import ThresholdManager
from app.config import AutoMemoryConfig
from app.extractors.action_extractor import extract_action_object
from app.extractors.entity_extractor import extract_entities
from app.extractors.technical_extractor import extract_time_info
from app.integrations.ollama_client import OllamaClient
from app.memory.indexer import MemoryIndexer
from app.memory.repository import MemoryRepository
from app.observability.logger import PipelineLogger
from app.observability.metrics import MetricsCollector
from app.observability.trace import TraceCollector
from app.pipeline.consistency import consistency_check
from app.pipeline.dedupe import dedupe_check
from app.pipeline.decision import final_decision
from app.pipeline.quality_gate import quality_gate
from app.review.review_service import ReviewService
from app.rules.block_rules import should_block
from app.rules.extraction_rules import extract_signals
from app.rules.preprocess_rules import preprocess_text
from app.rules.privacy_rules import privacy_check
from app.rules.registry import RuleRegistry
from app.rules.tag_rules import derive_aux_tags
from app.schemas.pipeline_schema import MemoryRecord, PipelineResult
from app.schemas.review_schema import ReviewItem


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
        self.indexer = MemoryIndexer(index_path)
        self.logger = PipelineLogger(log_path)
        self.metrics = MetricsCollector()
        self.review_service = ReviewService()
        self.context_builder = ContextBuilder()

        rule_classifier = RuleClassifier(self.registry.category_rules_sorted())
        llm_classifier = LLMClassifier(
            OllamaClient(config.ollama.base_url, config.ollama.model, config.ollama.timeout_seconds),
            categories=config.allow_categories,
        )
        self.hybrid_classifier = HybridClassifier(rule_classifier, llm_classifier, self.threshold_manager)

    def _mk_id(self, text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

    def process(self, text: str, source: str = "interaction") -> PipelineResult:
        trace = TraceCollector()
        dropped: List[Dict] = []
        records: List[MemoryRecord] = []

        # 1 preprocess
        normalized = preprocess_text(text)
        trace.add("preprocess", {"normalized_len": len(normalized)})

        # 2 quality_gate
        ok, quality_reason = quality_gate(normalized)
        trace.add("quality_gate", {"ok": ok, "reason": quality_reason})
        if not ok:
            self.metrics.inc("dropped_quality")
            return PipelineResult(status="dropped", dropped=[{"stage": "quality_gate", "reason": quality_reason}], trace_id=trace.trace_id, metrics=self.metrics.snapshot())

        # 3 privacy
        safe, privacy_hits = privacy_check(normalized)
        trace.add("privacy", {"safe": safe, "hits": privacy_hits})
        if not safe:
            self.metrics.inc("dropped_privacy")
            return PipelineResult(status="dropped", dropped=[{"stage": "privacy", "reason": privacy_hits}], trace_id=trace.trace_id, metrics=self.metrics.snapshot())

        # 4 block
        blocked, block_reason = should_block(normalized)
        trace.add("block", {"blocked": blocked, "reason": block_reason})
        if blocked:
            self.metrics.inc("dropped_block")
            return PipelineResult(status="dropped", dropped=[{"stage": "block", "reason": block_reason}], trace_id=trace.trace_id, metrics=self.metrics.snapshot())

        # 5 dedupe
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
            return PipelineResult(status="dropped", dropped=[{"stage": "hybrid_classifier", "reason": f"category_not_allowed:{category}"}], trace_id=trace.trace_id, metrics=self.metrics.snapshot())

        allow_write, dedupe_key, duplicate_of = dedupe_check(self.repo, category, normalized)
        if not allow_write:
            self.metrics.inc("dropped_duplicate")
            dropped.append({"stage": "dedupe", "reason": "duplicate", "duplicate_of": duplicate_of})
            trace.add("dedupe", {"duplicate": True, "duplicate_of": duplicate_of})
            return PipelineResult(status="dropped", dropped=dropped, trace_id=trace.trace_id, metrics=self.metrics.snapshot())
        trace.add("dedupe", {"duplicate": False, "dedupe_key": dedupe_key})

        # 8 extraction
        signals = extract_signals(normalized)
        tags = sorted(set(tags + derive_aux_tags(category, signals)))
        entities = extract_entities(normalized)
        action, obj, status = extract_action_object(normalized)
        time_info = extract_time_info(normalized)
        trace.add("extraction", {"signals": signals, "entities": entities, "action": action, "object": obj, "status": status})

        # 9 consistency
        consistent, conflict_flag, conflict_reason = consistency_check(normalized)
        trace.add("consistency", {"consistent": consistent, "conflict_flag": conflict_flag, "reason": conflict_reason})

        # 10 decision
        save_status, needs_review, review_reason = final_decision(
            category,
            confidence,
            self.threshold_manager,
            self.config.review_confidence_threshold,
            conflict_flag,
        )
        trace.add("decision", {"status": save_status, "needs_review": needs_review, "review_reason": review_reason})

        record = MemoryRecord(
            text=text,
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
                "signals": signals,
                "classifier_reason": cls.get("reason", ""),
                "llm_error": cls.get("llm_error", ""),
                "gray_reason": cls.get("gray_reason", ""),
            },
        )

        # 11 repository
        saved = self.repo.save(record)
        trace.add("repository", {"saved": True, "id": saved.get("meta", {}).get("id")})

        # 12 indexer incremental
        self.indexer.add(saved)
        trace.add("indexer", {"indexed": True})

        # 13 logger
        self.logger.log({"trace": trace.dump(), "memory": saved})
        trace.add("logger", {"logged": True})

        # 14 metrics
        self.metrics.inc("saved_total")
        if record.llm_used:
            self.metrics.inc("llm_used")
        if needs_review:
            self.metrics.inc("needs_review")
            self.review_service.enqueue(
                ReviewItem(
                    memory_id=str(saved.get("meta", {}).get("id", "")),
                    reason=review_reason,
                    confidence=confidence,
                    category=category,
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
