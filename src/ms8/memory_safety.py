"""Shared provenance, confidence, and pre-action governance rules."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

PROVENANCE_SCHEMA_VERSION = "1.0"
VERIFICATION_STATES = {"unverified", "user_asserted", "observed", "verified", "rejected"}
UNTRUSTED_AUTHORITIES = {"assistant_inferred", "tool_generated"}
BLOCKED_SENSITIVITIES = {"secret", "credential"}
BLOCKED_STATUSES = {"candidate", "pending_review", "quarantined", "stale", "superseded", "revoked"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _content_sha256(value: object) -> str:
    return hashlib.sha256(_normalized_text(value).encode("utf-8")).hexdigest()


def _source_kind(source: object) -> str:
    value = str(source or "unknown").strip().lower()
    if ":" in value:
        value = value.split(":", 1)[0]
    if value in {"ask", "user", "manual"}:
        return "user"
    if value.startswith("absorb"):
        return "local_document"
    if value.startswith("mcp"):
        return "mcp_client"
    if value in {"system", "doctor", "watch", "maintenance", "repair", "self_check"}:
        return "system"
    return value or "unknown"


def default_confidence(authority: object, status: object = "accepted") -> float:
    if str(status or "").strip().lower() == "verified":
        return 1.0
    return {
        "user_explicit": 0.9,
        "system_observed": 0.75,
        "user_implicit": 0.7,
        "tool_generated": 0.5,
        "assistant_inferred": 0.45,
    }.get(str(authority or "").strip().lower(), 0.5)


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _as_transformations(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append({str(k): v for k, v in item.items()})
        elif str(item).strip():
            out.append({"operation": str(item)})
    return out


def build_memory_provenance(
    *,
    text: object,
    source: object,
    record_id: object,
    authority: object,
    status: object = "accepted",
    created_at: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = metadata if isinstance(metadata, dict) else {}
    nested = meta.get("provenance", {}) if isinstance(meta.get("provenance", {}), dict) else {}
    recorded_at = str(nested.get("recorded_at") or meta.get("recorded_at") or created_at or _utc_now())
    source_kind = str(nested.get("source_kind") or meta.get("source_kind") or _source_kind(source))
    source_ref = str(
        nested.get("source_ref")
        or meta.get("source_ref")
        or f"{str(source or source_kind).strip()}:{str(record_id or '').strip()}"
    )
    authority_value = str(authority or "user_implicit").strip().lower()
    verification_state = (
        str(
            nested.get("verification_state")
            or meta.get("verification_state")
            or (
                "verified"
                if str(status or "").strip().lower() == "verified"
                else "user_asserted"
                if authority_value == "user_explicit"
                else "observed"
                if authority_value == "system_observed"
                else "unverified"
            )
        )
        .strip()
        .lower()
    )
    raw_confidence = nested.get(
        "confidence",
        meta.get("confidence", default_confidence(authority_value, status)),
    )
    try:
        confidence = float(default_confidence(authority_value, status) if raw_confidence is None else raw_confidence)
    except (TypeError, ValueError):
        confidence = default_confidence(authority_value, status)
    confidence = max(0.0, min(1.0, confidence))
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "content_sha256": _content_sha256(text),
        "created_by": str(nested.get("created_by") or meta.get("created_by") or source or authority_value),
        "recorded_by": str(nested.get("recorded_by") or meta.get("recorded_by") or "ms8"),
        "observed_at": str(nested.get("observed_at") or meta.get("observed_at") or recorded_at),
        "recorded_at": recorded_at,
        "valid_from": str(nested.get("valid_from") or meta.get("valid_from") or recorded_at),
        "valid_until": str(nested.get("valid_until") or meta.get("valid_until") or ""),
        "parent_record_ids": _as_string_list(nested.get("parent_record_ids", meta.get("parent_record_ids", []))),
        "transformations": _as_transformations(nested.get("transformations", meta.get("transformations", []))),
        "verification_state": verification_state,
        "confidence": confidence,
    }


def normalize_memory_provenance(record: dict[str, Any]) -> dict[str, Any]:
    existing = record.get("provenance", {}) if isinstance(record.get("provenance", {}), dict) else {}
    generated = build_memory_provenance(
        text=record.get("normalized_text") or record.get("text") or "",
        source=record.get("source") or "unknown",
        record_id=record.get("id") or "",
        authority=record.get("authority") or "user_implicit",
        status=record.get("status") or "accepted",
        created_at=str(record.get("created_at") or ""),
        metadata={"provenance": existing},
    )
    merged = dict(existing)
    for key, value in generated.items():
        if key not in merged or merged[key] in (None, "", []):
            merged[key] = value
    return merged


def validate_memory_provenance(record: dict[str, Any], provenance: object) -> tuple[bool, str]:
    if not isinstance(provenance, dict):
        return False, "invalid:provenance_type"
    required = (
        "schema_version",
        "source_kind",
        "source_ref",
        "content_sha256",
        "created_by",
        "recorded_by",
        "recorded_at",
        "verification_state",
        "confidence",
    )
    for key in required:
        if key not in provenance or provenance[key] in (None, ""):
            return False, f"missing:provenance.{key}"
    if str(provenance.get("schema_version")) != PROVENANCE_SCHEMA_VERSION:
        return False, "invalid:provenance_schema_version"
    if str(provenance.get("verification_state", "")) not in VERIFICATION_STATES:
        return False, "invalid:provenance_verification_state"
    raw_confidence = provenance.get("confidence")
    if raw_confidence is None:
        return False, "invalid:provenance_confidence"
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        return False, "invalid:provenance_confidence"
    if not 0.0 <= confidence <= 1.0:
        return False, "invalid:provenance_confidence_range"
    expected = _content_sha256(record.get("normalized_text") or record.get("text") or "")
    if str(provenance.get("content_sha256")) != expected:
        return False, "invalid:provenance_content_digest"
    return True, "ok"


def _is_expired(row: dict[str, Any], provenance: dict[str, Any]) -> bool:
    raw_value = row.get("valid_until") or row.get("ttl") or provenance.get("valid_until") or ""
    raw = str(raw_value).strip()
    if not raw:
        return False
    try:
        value = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc) < datetime.now(timezone.utc)
    except ValueError:
        return True


def _query_targets_system_debug(query: str) -> bool:
    lowered = str(query or "").lower()
    return any(
        hint in lowered
        for hint in (
            "ms8",
            "debug",
            "self-check",
            "maintenance",
            "governance",
            "policy",
            "shadow",
            "治理",
            "自检",
            "调试",
            "系统",
        )
    )


def evaluate_memory_policy(
    row: dict[str, Any],
    *,
    query: str = "",
    purpose: str = "recall",
) -> dict[str, Any]:
    lane = str(purpose or "recall").strip().lower()
    if lane not in {"recall", "browse", "inject", "action"}:
        lane = "recall"
    reasons: list[str] = []
    status = str(row.get("status", "")).strip().lower()
    provenance = normalize_memory_provenance(row)
    valid_provenance, provenance_reason = validate_memory_provenance(row, provenance)
    if not valid_provenance:
        reasons.append(provenance_reason)
    if row.get("can_recall", True) is False:
        reasons.append("recall_disabled")
    if status in BLOCKED_STATUSES:
        reasons.append(f"status:{status or 'missing'}")
    if str(row.get("superseded_by", "")).strip():
        reasons.append("superseded")
    if _is_expired(row, provenance):
        reasons.append("expired")
    sensitivity = str(row.get("sensitivity", "private")).strip().lower()
    if sensitivity in BLOCKED_SENSITIVITIES:
        reasons.append(f"sensitivity:{sensitivity}")
    authority = str(row.get("authority", "user_implicit")).strip().lower()
    verification_state = str(provenance.get("verification_state", "unverified")).strip().lower()
    if authority in UNTRUSTED_AUTHORITIES and verification_state != "verified":
        reasons.append("unverified_low_authority")
    raw_confidence = provenance.get("confidence", default_confidence(authority, status))
    try:
        confidence = float(default_confidence(authority, status) if raw_confidence is None else raw_confidence)
    except (TypeError, ValueError):
        confidence = default_confidence(authority, status)
    threshold = {"recall": 0.5, "browse": 0.5, "inject": 0.7, "action": 0.85}[lane]
    if confidence < threshold:
        reasons.append("low_confidence")
    scope = str(row.get("scope", "")).strip().lower()
    if scope == "labs":
        reasons.append("scope:labs")
    if scope == "system_debug" and not _query_targets_system_debug(query):
        reasons.append("scope:system_debug")
    category = str(row.get("category", "")).strip().lower()
    if category == "product_decision" and lane not in {"action", "browse"}:
        lowered = str(query or "").lower()
        if not any(
            hint in lowered
            for hint in (
                "方案",
                "策略",
                "决策",
                "优先级",
                "取舍",
                "发布",
                "路线",
                "plan",
                "decision",
                "tradeoff",
                "priority",
            )
        ):
            reasons.append("query_intent_mismatch")
    if lane == "inject":
        if row.get("can_inject", True) is False:
            reasons.append("injection_disabled")
        if status not in {"accepted", "verified"}:
            reasons.append("injection_status_not_eligible")
    if lane == "action":
        if row.get("can_act_on") is not True:
            reasons.append("action_not_authorized_by_record")
        if status != "verified":
            reasons.append("action_record_not_verified")
        if authority != "user_explicit":
            reasons.append("action_authority_not_user_explicit")
        if verification_state != "verified":
            reasons.append("action_provenance_not_verified")
        authorized_action = _normalized_text(row.get("authorized_action") or "")
        requested_action = _normalized_text(query)
        if not authorized_action:
            reasons.append("action_scope_missing")
        elif authorized_action.casefold() != requested_action.casefold():
            reasons.append("action_scope_mismatch")
    unique_reasons = list(dict.fromkeys(reasons))
    return {
        "allowed": not unique_reasons,
        "purpose": lane,
        "reason_codes": unique_reasons,
        "confidence": round(confidence, 6),
        "confidence_threshold": threshold,
        "verification_state": verification_state,
        "record_id": str(row.get("id") or ""),
    }


def pre_action_check(
    *,
    action: str,
    records: list[dict[str, Any]],
    memory_ids: list[str] | None = None,
    explicit_user_confirmation: bool = False,
) -> dict[str, Any]:
    action_text = _normalized_text(action)
    selected_ids = {str(item).strip() for item in (memory_ids or []) if str(item).strip()}
    selected = [row for row in records if str(row.get("id") or "") in selected_ids]
    decisions = [evaluate_memory_policy(row, query=action_text, purpose="action") for row in selected]
    eligible_ids = [str(item.get("record_id") or "") for item in decisions if item.get("allowed")]
    reason_counts: Counter[str] = Counter()
    for item in decisions:
        if not item.get("allowed"):
            reason_counts.update(str(code) for code in item.get("reason_codes", []))
    missing_ids = sorted(selected_ids - {str(row.get("id") or "") for row in selected})
    all_selected_eligible = bool(decisions) and len(eligible_ids) == len(decisions)
    if not action_text:
        reason_counts.update(["action_required"])
    if not selected_ids:
        reason_counts.update(["supporting_memory_required"])
    if missing_ids:
        reason_counts.update(["memory_id_not_found"])
    if all_selected_eligible and not explicit_user_confirmation:
        reason_counts.update(["human_confirmation_required"])
    allowed = bool(
        action_text and selected_ids and all_selected_eligible and explicit_user_confirmation and not missing_ids
    )
    return {
        "ok": True,
        "decision": "allow" if allowed else "deny",
        "allowed": allowed,
        "execution_performed": False,
        "action": action_text,
        "requires_confirmation": bool(all_selected_eligible and not explicit_user_confirmation),
        "explicit_user_confirmation": bool(explicit_user_confirmation),
        "eligible_record_ids": eligible_ids,
        "evaluated_record_ids": [str(row.get("id") or "") for row in selected],
        "missing_record_ids": missing_ids,
        "reason_counts": dict(sorted(reason_counts.items())),
        "record_decisions": decisions,
    }


def provenance_fingerprint(provenance: dict[str, Any]) -> str:
    payload = json.dumps(provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
