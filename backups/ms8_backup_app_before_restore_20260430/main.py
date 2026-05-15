from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .classifier import ThresholdManager
from .config import DedupeConfig, QualityGateConfig, ThresholdConfig
from .feedback import FeedbackService, RuleOptimizer
from .memory import MemoryIndexer, MemoryRepository
from .pipeline import dedupe_check, quality_gate
from .review import ReviewService
from .schemas import MemoryRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_id(text: str, source: str) -> str:
    seed = f"{source}:{text}".encode("utf-8")
    return "rec_" + hashlib.sha1(seed).hexdigest()[:16]


@dataclass
class PipelineResult:
    status: str
    trace_id: str
    accepted: int = 0
    pending: int = 0
    rejected: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    records: list[MemoryRecord] = field(default_factory=list)


class MemoryPipeline:
    def __init__(self, workspace_dir: Path, memory_settings: dict[str, Any] | None = None) -> None:
        settings = memory_settings or {}
        memory_dir = Path(workspace_dir) / "memory"
        self.repo = MemoryRepository(memory_dir / "auto_memory_records.jsonl")
        self.indexer = MemoryIndexer(memory_dir / "auto_memory_index.json")
        self.review = ReviewService(memory_dir / "auto_memory_review_queue.jsonl")
        self.feedback = FeedbackService(memory_dir / "auto_memory_feedback.jsonl")
        self.threshold = ThresholdManager(ThresholdConfig())
        self.optimizer = RuleOptimizer(self.feedback, self.threshold)
        self.quality_cfg = QualityGateConfig()
        self.dedupe_cfg = DedupeConfig()
        self.review_min_conf = float(settings.get("review_min_confidence", 0.55))

    def process(self, text: str, *, source: str = "interaction") -> PipelineResult:
        trace_id = "tr_" + hashlib.sha1(f"{_now()}:{source}:{text}".encode("utf-8")).hexdigest()[:12]
        clean = str(text or "").strip()
        if not clean:
            return PipelineResult(status="rejected", trace_id=trace_id, rejected=1, dropped=[{"reason": "empty_text"}])

        ok, reason = quality_gate(clean, self.quality_cfg)
        if not ok:
            return PipelineResult(status="rejected", trace_id=trace_id, rejected=1, dropped=[{"reason": reason}])

        keep, best, score, mode, _meta = dedupe_check(self.repo, "general", clean, self.dedupe_cfg)
        if not keep:
            return PipelineResult(
                status="duplicate",
                trace_id=trace_id,
                rejected=1,
                dropped=[{"reason": mode, "score": score, "best": best}],
                metrics={"dedupe_score": score},
            )

        rec = MemoryRecord(
            text=clean,
            normalized_text=clean,
            source=source,
            category="general",
            status="accepted",
            confidence=max(0.5, min(1.0, float(score or 0.9))),
            meta={"id": _record_id(clean, source), "admission": "memory_pipeline_v1"},
        )
        self.repo.save(rec)
        self.indexer.add(
            {
                "id": rec.meta.get("id", ""),
                "normalized_text": rec.normalized_text,
                "source": rec.source,
                "confidence": rec.confidence,
                "created_at": _now(),
            }
        )
        pending = 0
        if float(rec.confidence) < self.review_min_conf:
            self.review.enqueue(
                {
                    "memory_id": rec.meta.get("id", ""),
                    "reason": "low_confidence",
                    "confidence": rec.confidence,
                    "category": rec.category,
                    "risk_level": "normal",
                    "decision": "pending",
                }
            )
            pending = 1
        return PipelineResult(
            status="accepted",
            trace_id=trace_id,
            accepted=1,
            pending=pending,
            metrics={"dedupe_score": score},
            records=[rec],
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
    ) -> dict[str, Any]:
        self.feedback.add(
            {
                "memory_id": memory_id,
                "category": category,
                "signal": signal,
                "helpful": bool(helpful),
                "note": note,
                "source": source,
                "confidence": float(confidence or 0.0),
                "timestamp": _now(),
            }
        )
        return {"status": "recorded"}

    def weekly_threshold_suggestions(self) -> dict[str, Any]:
        return self.optimizer.suggest_threshold_updates()

    def cleanup_test_pollution(self) -> dict[str, Any]:
        return {"status": "success", "removed": 0}


def build_pipeline(workspace_dir: Path | str, memory_settings: dict[str, Any] | None = None) -> MemoryPipeline:
    return MemoryPipeline(Path(workspace_dir), memory_settings=memory_settings)

