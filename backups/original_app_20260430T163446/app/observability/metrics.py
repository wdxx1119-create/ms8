from __future__ import annotations

from collections import Counter
from typing import Dict


class MetricsCollector:
    def __init__(self) -> None:
        self.counter = Counter()

    def inc(self, name: str, value: int = 1) -> None:
        self.counter[name] += value

    def snapshot(self) -> Dict[str, int]:
        return dict(self.counter)
