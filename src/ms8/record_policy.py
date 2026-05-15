"""Canonical memory record policy for MS8."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ALLOWED_STATUS = {
    "candidate",
    "short_term",
    "accepted",
    "verified",
    "pending_review",
    "quarantined",
    "stale",
    "superseded",
    "revoked",
}

ALLOWED_TRANSITIONS = {
    "candidate": {"short_term", "accepted", "pending_review", "quarantined", "revoked"},
    "short_term": {"accepted", "pending_review", "stale", "revoked"},
    "accepted": {"verified", "pending_review", "stale", "superseded", "revoked", "quarantined"},
    "verified": {"stale", "superseded", "revoked", "quarantined"},
    "pending_review": {"accepted", "verified", "quarantined", "revoked"},
    "quarantined": {"pending_review", "revoked"},
    "stale": {"accepted", "verified", "superseded", "revoked"},
    "superseded": {"revoked"},
    "revoked": set(),
}

DEBUG_KEYWORDS = (
    "self-check",
    "compression",
    "doctor",
    "dashboard",
    "watch",
    "shadow",
    "governance",
    "threshold",
    "admission",
    "repair",
    "test",
    "pytest",
    "debug",
    "module not found",
    "traceback",
)

PREFERENCE_KEYWORDS = (
    "我喜欢",
    "我习惯",
    "偏好",
    "prefer",
    "preference",
)

PRODUCT_DECISION_KEYWORDS = (
    "方案",
    "策略",
    "决策",
    "决定",
    "路线",
    "优先级",
    "发布",
    "开关",
    "取舍",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def infer_scope_flags(text: str, source: str) -> dict[str, Any]:
    content = normalize_text(text)
    low = content.lower()
    src = str(source or "").strip().lower()
    debug_sources = {
        "system",
        "doctor",
        "dashboard",
        "watch",
        "maintenance",
        "self_check",
        "self-repair",
        "repair",
        "shadow",
        "test",
        "verification",
        "synthetic",
    }
    content_debug = any(k in low for k in DEBUG_KEYWORDS)
    is_labs = src.startswith("labs") or src.startswith("experimental")
    is_debug = (src in debug_sources) or (src in {"maintenance_sync", "system"} and content_debug)
    is_preference = any(k in content for k in PREFERENCE_KEYWORDS) or any(k in low for k in PREFERENCE_KEYWORDS)
    is_product_decision = any(k in content for k in PRODUCT_DECISION_KEYWORDS) or any(
        k in low for k in PRODUCT_DECISION_KEYWORDS
    )

    if is_labs:
        scope = "labs"
        authority = "assistant_inferred"
        sensitivity = "internal"
        can_recall = True
        can_inject = False
    elif is_debug and is_product_decision:
        # Self-referential product decisions: keep recallable, inject with lower priority in upper layers.
        scope = "project"
        authority = "system_observed"
        sensitivity = "internal"
        can_recall = True
        can_inject = True
    elif is_debug:
        # Pure system debug: never normal inject.
        scope = "system_debug"
        authority = "system_observed"
        sensitivity = "internal"
        can_recall = True
        can_inject = False
    elif is_preference:
        scope = "personal"
        authority = "user_explicit" if source == "ask" else "user_implicit"
        sensitivity = "private"
        can_recall = True
        can_inject = True
    else:
        scope = "personal"
        authority = "user_explicit" if source == "ask" else "system_observed"
        sensitivity = "private"
        can_recall = True
        can_inject = True

    return {
        "scope": scope,
        "authority": authority,
        "sensitivity": sensitivity,
        "can_recall": can_recall,
        "can_inject": can_inject,
        "can_act_on": False,
    }


def build_canonical_record(text: str, source: str, status: str = "accepted") -> dict[str, Any]:
    payload = infer_scope_flags(text=text, source=source)
    normalized = normalize_text(text)
    low = normalize_text(text).lower()
    if payload["scope"] == "labs":
        category = "experimental_note"
    elif payload["scope"] == "system_debug":
        category = "system_diagnostic"
    elif payload["scope"] == "project" and any(k in low for k in PRODUCT_DECISION_KEYWORDS):
        category = "product_decision"
    elif any(k in low for k in PREFERENCE_KEYWORDS):
        category = "user_preference"
    else:
        category = "general"
    return {
        "id": str(uuid4()),
        "text": normalized,
        "normalized_text": normalized,
        "category": category,
        "status": status if status in ALLOWED_STATUS else "candidate",
        "source": source,
        "created_at": _utc_now(),
        "meta": {"admission": "ms8_write_guard_v1"},
        **payload,
    }


def validate_record(record: dict[str, Any]) -> tuple[bool, str]:
    required = ("id", "normalized_text", "category", "status", "source", "meta")
    for key in required:
        if key not in record:
            return False, f"missing:{key}"
    if not isinstance(record.get("meta"), dict):
        return False, "invalid:meta_type"
    if "admission" not in record["meta"]:
        return False, "missing:meta.admission"
    if str(record.get("status", "")) not in ALLOWED_STATUS:
        return False, "invalid:status"
    governance_required = (
        "scope",
        "authority",
        "sensitivity",
        "can_recall",
        "can_inject",
        "can_act_on",
    )
    for key in governance_required:
        if key not in record:
            return False, f"missing:{key}"
    for key in ("can_recall", "can_inject", "can_act_on"):
        if not isinstance(record.get(key), bool):
            return False, f"invalid:{key}_type"
    scope = str(record.get("scope", "")).strip().lower()
    if scope == "system_debug" and record.get("can_inject") is True:
        return False, "invalid:debug_can_inject"
    return True, "ok"


def is_valid_status_transition(old_status: str | None, new_status: str) -> bool:
    old = str(old_status or "").strip().lower()
    new = str(new_status or "").strip().lower()
    if new not in ALLOWED_STATUS:
        return False
    if not old:
        return True
    if old == new:
        return True
    return new in ALLOWED_TRANSITIONS.get(old, set())


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_quarantine(path: Path, raw: dict[str, Any], reason: str) -> None:
    row = {"at": _utc_now(), "reason": reason, "record": raw}
    _append_jsonl(path, row)


def append_canonical_record(
    *,
    records_file: Path,
    quarantine_file: Path,
    text: str,
    source: str,
    status: str = "accepted",
) -> tuple[dict[str, Any], bool, str]:
    row = build_canonical_record(text=str(text or ""), source=str(source or "unknown"), status=status)
    ok, reason = validate_record(row)
    if not ok:
        append_quarantine(quarantine_file, row, reason)
        # Last-resort structural fallback; still canonical-shaped.
        row = build_canonical_record(text=str(text or ""), source=str(source or "unknown"), status="candidate")
        row["meta"]["admission"] = "ms8_write_guard_v1_fallback"
        ok, reason = validate_record(row)
    _append_jsonl(records_file, row)
    return row, ok, reason


def validate_file_and_quarantine(records_file: Path, quarantine_file: Path) -> None:
    if not records_file.exists():
        return
    kept: list[str] = []
    for line in records_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            append_quarantine(quarantine_file, {"raw_line": line}, "invalid_json")
            continue
        if not isinstance(row, dict):
            append_quarantine(quarantine_file, {"raw": row}, "invalid_type")
            continue
        ok, reason = validate_record(row)
        if not ok:
            append_quarantine(quarantine_file, row, reason)
            continue
        kept.append(json.dumps(row, ensure_ascii=False))
    records_file.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


def _looks_like_system_debug(text: str, source: str) -> bool:
    low = normalize_text(text).lower()
    src = str(source or "").strip().lower()
    debug_sources = {
        "system",
        "doctor",
        "dashboard",
        "watch",
        "maintenance",
        "self_check",
        "self-repair",
        "repair",
        "shadow",
        "test",
        "verification",
        "synthetic",
    }
    return src in debug_sources or any(k in low for k in DEBUG_KEYWORDS)


def _field_complete_count(rows: list[dict[str, Any]], field: str) -> int:
    total = 0
    for row in rows:
        if field in row and row[field] not in (None, "", []):
            total += 1
    return total


def repair_scope_flags(records_file: Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Backfill governance fields for old records (idempotent).

    Rules:
    - Fill missing governance fields.
    - Keep existing valid values.
    - Repair illegal combinations (e.g. system_debug + can_inject=true).
    """
    if not records_file.exists():
        return {"total": 0, "updated": 0, "system_debug": 0, "can_inject_false": 0}

    rows = records_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    out: list[str] = []
    parsed: list[dict[str, Any]] = []
    total = 0
    updated = 0
    system_debug = 0
    can_inject_false = 0
    suspicious_samples: list[dict[str, str]] = []
    for line in rows:
        if not line.strip():
            continue
        total += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if not isinstance(row, dict):
            out.append(line)
            continue

        text = str(row.get("text") or row.get("normalized_text") or "")
        source = str(row.get("source") or "unknown")
        flags = infer_scope_flags(text=text, source=source)
        row_scope = str(row.get("scope") or "").strip().lower()

        changed = False
        for k, v in flags.items():
            if k not in row:
                row[k] = v
                changed = True

        # Keep compatibility: ensure meta.admission exists for governance checks.
        meta = row.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            row["meta"] = meta
            changed = True
        if "admission" not in meta:
            meta["admission"] = "ms8_repair_scope_v1"
            changed = True

        if "schema_version" not in row:
            row["schema_version"] = "1.0"
            changed = True
        if "migration_version" not in row:
            row["migration_version"] = "p0_2_v1"
            changed = True

        # Fix illegal combination: system_debug should not inject/act.
        if row_scope == "system_debug" or _looks_like_system_debug(text, source):
            if row.get("scope") != "system_debug":
                row["scope"] = "system_debug"
                changed = True
            if row.get("can_inject") is not False:
                row["can_inject"] = False
                changed = True
            if row.get("can_act_on") is not False:
                row["can_act_on"] = False
                changed = True
            if row.get("can_recall") is not True:
                row["can_recall"] = True
                changed = True
            if row.get("category") not in (
                "system_diagnostic",
                "test_record",
                "implementation_note",
            ):
                if len(suspicious_samples) < 20:
                    suspicious_samples.append(
                        {
                            "id": str(row.get("id") or ""),
                            "category": str(row.get("category") or ""),
                            "text_preview": normalize_text(text)[:120],
                        }
                    )
        else:
            low = normalize_text(text).lower()
            if any(k in low for k in PRODUCT_DECISION_KEYWORDS):
                if str(row.get("scope") or "").strip().lower() in {"", "personal"}:
                    row["scope"] = "project"
                    row["sensitivity"] = "internal"
                    row["can_recall"] = True
                    row["can_inject"] = True
                    row["can_act_on"] = False
                    row["category"] = "product_decision"
                    changed = True
            elif any(k in low for k in PREFERENCE_KEYWORDS):
                if str(row.get("category") or "").strip().lower() in {"", "general"}:
                    row["category"] = "user_preference"
                    changed = True

        if str(row.get("scope")) == "system_debug":
            system_debug += 1
        if row.get("can_inject") is False:
            can_inject_false += 1

        if changed:
            updated += 1
        parsed.append(row)
        out.append(json.dumps(row, ensure_ascii=False))

    if not dry_run:
        records_file.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")

    required_fields = [
        "scope",
        "authority",
        "sensitivity",
        "can_recall",
        "can_inject",
        "can_act_on",
        "schema_version",
        "migration_version",
    ]
    completeness = {f: (_field_complete_count(parsed, f) / len(parsed) if parsed else 1.0) for f in required_fields}
    return {
        "total": total,
        "updated": updated,
        "system_debug": system_debug,
        "can_inject_false": can_inject_false,
        "field_completeness": completeness,
        "suspicious_samples": suspicious_samples,
        "mode": "dry_run" if dry_run else "apply",
    }
