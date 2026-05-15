from __future__ import annotations

import json
from pathlib import Path


class PipelineLogger:
    def __init__(
        self,
        log_path: Path,
        excluded_source_prefixes: list[str] | None = None,
        max_bytes: int = 1_500_000,
    ) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.excluded_source_prefixes = [
            str(x).strip().lower() for x in (excluded_source_prefixes or []) if str(x).strip()
        ]
        self.max_bytes = int(max_bytes)

    def _source_of(self, payload: dict) -> str:
        memory = payload.get("memory", {})
        return str(memory.get("source", "")).strip().lower()

    def _should_skip(self, payload: dict) -> bool:
        source = self._source_of(payload)
        return any(source.startswith(prefix) for prefix in self.excluded_source_prefixes)

    def _rotate_if_needed(self) -> None:
        if not self.log_path.exists():
            return
        if self.log_path.stat().st_size < max(200_000, self.max_bytes):
            return
        archived = self.log_path.with_name(self.log_path.stem + ".archived.log")
        if archived.exists():
            archived.unlink()
        self.log_path.rename(archived)

    def log(self, payload: dict) -> None:
        if self._should_skip(payload):
            return
        self._rotate_if_needed()
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
