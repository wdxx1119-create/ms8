from __future__ import annotations

from pathlib import Path
from typing import Any

from ms8.app.config import DedupeConfig, QualityGateConfig
from ms8.app.memory import MemoryRepository
from ms8.app.pipeline.dedupe import dedupe_check
from ms8.app.pipeline.quality_gate import quality_gate


class MemoryAdmissionEngine:
    """Lightweight admission gate for memory writes."""

    def __init__(
        self,
        records_path: Path,
        *,
        quality_cfg: QualityGateConfig | None = None,
        dedupe_cfg: DedupeConfig | None = None,
    ) -> None:
        self.records_path = Path(records_path)
        self.repo = MemoryRepository(self.records_path)
        self.quality_cfg = quality_cfg or QualityGateConfig()
        self.dedupe_cfg = dedupe_cfg or DedupeConfig()

    def admit(self, text: str, *, category: str = "general") -> dict[str, Any]:
        allowed, reason = quality_gate(text, self.quality_cfg)
        if not allowed:
            return {
                "allowed": False,
                "reason": f"quality_gate:{reason}",
                "duplicate": False,
                "best_match": None,
                "score": 0.0,
            }
        keep, best, score, mode, meta = dedupe_check(self.repo, category, text, self.dedupe_cfg)
        if not keep:
            return {
                "allowed": False,
                "reason": f"dedupe:{mode}",
                "duplicate": True,
                "best_match": best,
                "score": float(score),
                "meta": meta,
            }
        return {
            "allowed": True,
            "reason": "ok",
            "duplicate": False,
            "best_match": best,
            "score": float(score),
            "meta": meta,
        }
