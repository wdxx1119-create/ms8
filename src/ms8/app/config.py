from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_CATEGORIES = [
    "work_report",
    "plan",
    "decision",
    "configuration",
    "technical_doc",
    "test_result",
    "preference",
]


@dataclass
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.2:3b"
    timeout_seconds: int = 8


@dataclass
class ThresholdConfig:
    global_min_confidence: float = 0.55
    category_thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "work_report": 0.62,
            "plan": 0.58,
            "decision": 0.65,
            "configuration": 0.68,
            "technical_doc": 0.64,
            "test_result": 0.60,
            "preference": 0.57,
        }
    )
    low_confidence_review_min: float = 0.60
    cjk_ratio_threshold: float = 0.30
    hybrid_context_dependency_min_confidence: float = 0.80
    feedback_low_ratio: float = 0.40
    feedback_high_ratio: float = 0.75
    feedback_raise_step: float = 0.03
    feedback_drop_step: float = 0.02
    feedback_floor_threshold: float = 0.45
    index_hot_min_confidence: float = 0.65


@dataclass
class QualityGateConfig:
    min_len_cjk: int = 4
    min_len_non_cjk: int = 8
    max_len: int = 4000
    noisy_ratio_max: float = 0.35


@dataclass
class DedupeConfig:
    hard_block_window_minutes: int = 5
    hard_block_repeat_threshold: int = 3
    similar_window_minutes: int = 60
    similar_soft_threshold: float = 0.9
    similar_hard_threshold: float = 0.97
    category_repeat_thresholds: dict[str, int] = field(
        default_factory=lambda: {
            "work_report": 2,
            "plan": 4,
            "decision": 5,
            "configuration": 5,
            "technical_doc": 3,
            "test_result": 3,
            "preference": 6,
        }
    )


@dataclass
class AutoMemoryConfig:
    enabled: bool = True
    use_llm: bool = False
    max_per_interaction: int = 5
    review_confidence_threshold: float = 0.62
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    quality_gate: QualityGateConfig = field(default_factory=QualityGateConfig)
    dedupe: DedupeConfig = field(default_factory=DedupeConfig)
    allow_categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    exclude_source_prefixes_for_index: list[str] = field(default_factory=lambda: ["verify_canary", "test"])
    exclude_source_prefixes_for_log: list[str] = field(default_factory=lambda: ["verify_canary", "test"])
    pipeline_log_max_bytes: int = 1_500_000

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AutoMemoryConfig:
        data = dict(raw or {})
        ollama_raw = data.get("ollama", {})
        threshold_raw = data.get("thresholds", {})
        qg_raw = data.get("quality_gate", {})
        dedupe_raw = data.get("dedupe", {})
        return cls(
            enabled=bool(data.get("enabled", True)),
            use_llm=bool(data.get("use_llm", False)),
            max_per_interaction=int(data.get("max_per_interaction", 5)),
            review_confidence_threshold=float(data.get("review_confidence_threshold", 0.62)),
            ollama=OllamaConfig(
                base_url=str(ollama_raw.get("base_url", "http://127.0.0.1:11434")),
                model=str(ollama_raw.get("model", "llama3.2:3b")),
                timeout_seconds=int(ollama_raw.get("timeout_seconds", 8)),
            ),
            thresholds=ThresholdConfig(
                global_min_confidence=float(threshold_raw.get("global_min_confidence", 0.55)),
                category_thresholds=dict(
                    threshold_raw.get("category_thresholds", ThresholdConfig().category_thresholds)
                ),
                low_confidence_review_min=float(threshold_raw.get("low_confidence_review_min", 0.60)),
                cjk_ratio_threshold=float(
                    threshold_raw.get("cjk_ratio_threshold", qg_raw.get("cjk_ratio_threshold", 0.30))
                ),
                hybrid_context_dependency_min_confidence=float(
                    threshold_raw.get("hybrid_context_dependency_min_confidence", 0.80)
                ),
                feedback_low_ratio=float(threshold_raw.get("feedback_low_ratio", 0.40)),
                feedback_high_ratio=float(threshold_raw.get("feedback_high_ratio", 0.75)),
                feedback_raise_step=float(threshold_raw.get("feedback_raise_step", 0.03)),
                feedback_drop_step=float(threshold_raw.get("feedback_drop_step", 0.02)),
                feedback_floor_threshold=float(threshold_raw.get("feedback_floor_threshold", 0.45)),
                index_hot_min_confidence=float(threshold_raw.get("index_hot_min_confidence", 0.65)),
            ),
            quality_gate=QualityGateConfig(
                min_len_cjk=int(qg_raw.get("min_len_cjk", 4)),
                min_len_non_cjk=int(qg_raw.get("min_len_non_cjk", 8)),
                max_len=int(qg_raw.get("max_len", 4000)),
                noisy_ratio_max=float(qg_raw.get("noisy_ratio_max", 0.35)),
            ),
            dedupe=DedupeConfig(
                hard_block_window_minutes=int(dedupe_raw.get("hard_block_window_minutes", 5)),
                hard_block_repeat_threshold=int(dedupe_raw.get("hard_block_repeat_threshold", 3)),
                similar_window_minutes=int(dedupe_raw.get("similar_window_minutes", 60)),
                similar_soft_threshold=float(dedupe_raw.get("similar_soft_threshold", 0.9)),
                similar_hard_threshold=float(dedupe_raw.get("similar_hard_threshold", 0.97)),
                category_repeat_thresholds=dict(
                    dedupe_raw.get("category_repeat_thresholds", DedupeConfig().category_repeat_thresholds)
                ),
            ),
            allow_categories=list(data.get("allow_categories", DEFAULT_CATEGORIES)),
            exclude_source_prefixes_for_index=list(
                data.get("exclude_source_prefixes_for_index", ["verify_canary", "test"])
            ),
            exclude_source_prefixes_for_log=list(
                data.get("exclude_source_prefixes_for_log", ["verify_canary", "test"])
            ),
            pipeline_log_max_bytes=int(data.get("pipeline_log_max_bytes", 1_500_000)),
        )
