from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


class ThresholdManager:
    def __init__(self, config: Any) -> None:
        if is_dataclass(config):
            cfg = asdict(config)
        elif isinstance(config, dict):
            cfg = dict(config)
        else:
            cfg = {}
        self.accept_confidence = float(cfg.get("accept_confidence", 0.62))
        self.reject_confidence = float(cfg.get("reject_confidence", 0.20))

    def snapshot(self) -> dict[str, float]:
        return {
            "accept_confidence": self.accept_confidence,
            "reject_confidence": self.reject_confidence,
        }
