"""Unified MS8 path resolution."""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def get_ms8_home() -> Path:
    env_home = _env_path("MS8_HOME")
    if env_home is not None:
        return env_home
    modern = Path.home() / ".ms8"
    legacy = Path.home() / ".ms8_runtime"
    # Prefer the directory that already has real runtime payload to avoid split-brain.
    def _score(root: Path) -> int:
        score = 0
        markers = [
            (root / "memory" / "auto_memory_records.jsonl", 4),
            (root / "memory" / "knowledge_graph.db", 3),
            (root / "data" / "memories.jsonl", 2),
            (root / "memory" / "auto_memory_index.json", 1),
        ]
        for path, weight in markers:
            if path.exists():
                score += weight
        return score

    modern_score = _score(modern)
    legacy_score = _score(legacy)
    if modern_score > 0 or legacy_score > 0:
        return modern if modern_score >= legacy_score else legacy
    if modern.exists() and not legacy.exists():
        return modern
    if legacy.exists() and not modern.exists():
        return legacy
    return modern


def get_data_dir() -> Path:
    return _env_path("MS8_DATA_DIR") or (get_ms8_home() / "data")


def get_config_dir() -> Path:
    return _env_path("MS8_CONFIG_DIR") or (get_ms8_home() / "config")


def get_log_dir() -> Path:
    return _env_path("MS8_LOG_DIR") or (get_ms8_home() / "logs")


def get_health_dir() -> Path:
    return get_ms8_home() / "health"


def detect_install_mode() -> str:
    # Best-effort classification for doctor output.
    this_file = Path(__file__).resolve()
    text = str(this_file)
    if "site-packages" in text:
        return "wheel"
    if "/src/ms8/" in text:
        return "source"
    return "unknown"
