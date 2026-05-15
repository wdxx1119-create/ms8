from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from app.config import AutoMemoryConfig
from app.pipeline.memory_pipeline import MemoryPipeline


def build_pipeline(workspace_dir: Path, memory_settings: Dict[str, Any]) -> MemoryPipeline:
    auto_cfg = AutoMemoryConfig.from_dict(memory_settings.get("auto_memory", {}))
    return MemoryPipeline(workspace_dir=workspace_dir, config=auto_cfg)
