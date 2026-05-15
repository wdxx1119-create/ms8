from __future__ import annotations

from dataclasses import asdict

from ms8.app.schemas.pipeline_schema import MemoryRecord


def record_to_dict(record: MemoryRecord) -> dict:
    return asdict(record)
