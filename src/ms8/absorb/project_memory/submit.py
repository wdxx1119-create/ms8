"""Controlled main-memory submission helpers for project-memory."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..governance import submit_to_ms8_governed
from .scope import update_project_fields


def _summary_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_summary_text(project_name: str, project_root: Path, summary_text: str) -> str:
    body = summary_text.strip()
    return (
        f"Project memory summary [{project_name}]:\n"
        f"root={project_root}\n\n"
        f"{body}"
    ).strip()


def submit_project_summary(
    *,
    project_name: str,
    project_root: Path,
    output_dir: Path,
    previous_hash: str = "",
    force: bool = False,
) -> dict[str, Any]:
    summary_path = output_dir / "project_summary.md"
    if not summary_path.exists():
        return {
            "ok": False,
            "status": "missing_summary",
            "reason": "build project outputs first",
            "next_actions": [f"ms8 absorb project-memory build --name {project_name}"],
        }

    raw = summary_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not raw:
        return {
            "ok": False,
            "status": "empty_summary",
            "reason": "project summary output is empty",
        }

    memory_text = _build_summary_text(project_name, project_root, raw)
    content_hash = _summary_hash(memory_text)
    if not force and previous_hash and previous_hash == content_hash:
        return {
            "ok": True,
            "status": "noop",
            "reason": "summary_unchanged",
            "project_name": project_name,
            "content_hash": content_hash,
        }

    result = submit_to_ms8_governed(
        memory_text,
        {
            "source_system": "project_memory",
            "project_name": project_name,
            "project_root": str(project_root),
            "summary_path": str(summary_path),
            "content_hash": content_hash,
        },
    )
    if not bool(result.get("ok", False)):
        return {
            "ok": False,
            "status": "submit_failed",
            "project_name": project_name,
            "result": result,
        }

    record = dict(result.get("record", {}) or {})
    record_id = str(record.get("id", "") or "")
    update_project_fields(
        project_name,
        last_summary_hash=content_hash,
        last_summary_record_id=record_id,
        last_summary_submitted_at=str(record.get("updated_at", "") or record.get("created_at", "") or ""),
    )
    return {
        "ok": True,
        "status": "submitted",
        "project_name": project_name,
        "content_hash": content_hash,
        "record_id": record_id,
        "result": result,
    }
