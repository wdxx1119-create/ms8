"""Governance checks for absorb chunks."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .repository import quarantine_dir

SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |ENCRYPTED )?PRIVATE KEY-----"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}"),
    "api_key": re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*[^\s]{8,}"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
}
PII_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"\b(?:\+?\d[\d -]{7,}\d)\b"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(text: str) -> str:
    value = str(text or "")
    for name, pattern in {**SECRET_PATTERNS, **PII_PATTERNS}.items():
        value = pattern.sub(f"[REDACTED_{name.upper()}]", value)
    return value[:300]


def run_absorb_governance(chunk: str, metadata: dict[str, Any]) -> dict[str, Any]:
    text = str(chunk or "")
    secret_hits = [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]
    pii_hits = [name for name, pattern in PII_PATTERNS.items() if pattern.search(text)]
    if secret_hits:
        return {
            "decision": "quarantine",
            "risk_level": "high",
            "matched_rule": ",".join(secret_hits),
            "redacted_preview": _redact(text),
            "reason": "secret_or_financial_pattern",
        }
    if pii_hits:
        return {
            "decision": "pending_review",
            "risk_level": "medium",
            "matched_rule": ",".join(pii_hits),
            "redacted_preview": _redact(text),
            "reason": "pii_requires_review",
        }
    return {
        "decision": "local_index",
        "risk_level": "low",
        "matched_rule": "",
        "redacted_preview": _redact(text),
        "reason": "low_risk",
    }


def write_quarantine_metadata(
    *,
    file_id: str,
    chunk_index: int,
    source_path: str,
    content_hash: str,
    chunk_hash: str,
    governance: dict[str, Any],
) -> Path:
    quarantine_dir().mkdir(parents=True, exist_ok=True)
    path = quarantine_dir() / f"{file_id}_{chunk_index}.json"
    payload = {
        "source_path": source_path,
        "content_hash": content_hash,
        "chunk_hash": chunk_hash,
        "risk_type": governance.get("risk_level", "unknown"),
        "matched_rule": governance.get("matched_rule", ""),
        "redacted_preview": governance.get("redacted_preview", ""),
        "created_at": _now(),
        "decision": governance.get("decision", ""),
        "reason": governance.get("reason", ""),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def submit_to_ms8_governed(summary_or_memory: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Submit a document summary through the public runtime write path.

    This remains opt-in from the CLI so absorb chunks do not flood main memory.
    """
    from ..runtime import ensure_runtime_dirs, write_memory

    text = str(summary_or_memory or "").strip()
    if not text:
        return {"ok": False, "reason": "empty_summary"}
    safe_meta = dict(metadata or {})
    safe_meta["source_system"] = "absorb"
    row = write_memory(text, source="absorb")
    record_id = str(row.get("id", "") or "")
    if record_id:
        _tag_absorb_record(ensure_runtime_dirs()["memories"], record_id, safe_meta)
        row.setdefault("meta", {})
        if isinstance(row["meta"], dict):
            row["meta"].update({"source_system": "absorb", "absorb": safe_meta})
    return {"ok": True, "record": row, "metadata": metadata}


def _tag_absorb_record(records_file: Path, record_id: str, metadata: dict[str, Any]) -> bool:
    """Tag an already-written main-memory record as absorb-originated.

    The public runtime write API intentionally stays small (text/source only),
    so absorb adds source metadata after the governed write succeeds.
    """
    if not records_file.exists():
        return False
    changed = False
    lines: list[str] = []
    for raw in records_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            lines.append(raw)
            continue
        if isinstance(row, dict) and str(row.get("id", "") or "") == record_id:
            meta = row.setdefault("meta", {})
            if not isinstance(meta, dict):
                meta = {}
                row["meta"] = meta
            meta["source_system"] = "absorb"
            meta["absorb"] = dict(metadata or {})
            changed = True
        lines.append(json.dumps(row, ensure_ascii=False) if isinstance(row, dict) else raw)
    if changed:
        tmp = records_file.with_suffix(records_file.suffix + ".absorb_tag_tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(records_file)
    return changed
