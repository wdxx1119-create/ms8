from __future__ import annotations

from pathlib import Path
from typing import Any

from ms8.app.config import AutoMemoryConfig
from ms8.app.pipeline.memory_pipeline import MemoryPipeline


def build_pipeline(workspace_dir: Path, memory_settings: dict[str, Any]) -> MemoryPipeline:
    auto_cfg = AutoMemoryConfig.from_dict(memory_settings.get("auto_memory", {}))
    return MemoryPipeline(workspace_dir=workspace_dir, config=auto_cfg)
