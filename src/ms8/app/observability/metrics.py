from __future__ import annotations

from collections import Counter


class MetricsCollector:
    def __init__(self) -> None:
        self.counter: Counter[str] = Counter()

    def inc(self, name: str, value: int = 1) -> None:
        self.counter[name] += value

    def snapshot(self) -> dict[str, int]:
        return dict(self.counter)
