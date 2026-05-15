from __future__ import annotations

from dataclasses import asdict
from typing import Dict

from app.schemas.pipeline_schema import MemoryRecord


def record_to_dict(record: MemoryRecord) -> Dict:
    return asdict(record)
