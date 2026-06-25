from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

BROWSABLE_STATUSES = {"short_term", "accepted", "verified"}
BLOCKED_SENSITIVITIES = {"secret", "credential"}
UNTRUSTED_AUTHORITIES = {"assistant_inferred", "tool_generated"}
BLOCKED_SCOPES = {"labs", "system_debug"}
SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "token",
    "access_token",
}


def _is_expired(row: dict[str, Any]) -> bool:
    valid_until = str(row.get("valid_until") or row.get("ttl") or "").strip()
    if not valid_until:
        return False
    try:
        raw = valid_until[:-1] + "+00:00" if valid_until.endswith("Z") else valid_until
        value = datetime.fromisoformat(raw)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc) < datetime.now(timezone.utc)
    except ValueError:
        return True


def memory_row_browsable(row: dict[str, Any]) -> bool:
    """Return whether a record is safe for default explicit MCP browsing."""

    status = str(row.get("status", "")).strip().lower()
    if status not in BROWSABLE_STATUSES:
        return False
    if row.get("can_recall", True) is False:
        return False
    if str(row.get("superseded_by", "")).strip():
        return False
    if _is_expired(row):
        return False
    sensitivity = str(row.get("sensitivity", "private")).strip().lower()
    if sensitivity in BLOCKED_SENSITIVITIES:
        return False
    authority = str(row.get("authority", "user_implicit")).strip().lower()
    if authority in UNTRUSTED_AUTHORITIES and status != "verified":
        return False
    scope = str(row.get("scope", "")).strip().lower()
    if scope in BLOCKED_SCOPES:
        return False
    return True


def redact_memory_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a defensive copy with credentials and secret payloads removed."""

    sensitivity = str(row.get("sensitivity", "private")).strip().lower()

    def redact(value: Any, key: str = "") -> Any:
        normalized_key = key.strip().lower()
        if normalized_key in SENSITIVE_KEYS:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k): redact(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return [redact(item) for item in value]
        return value

    safe = redact(dict(row))
    if not isinstance(safe, dict):
        return {"redacted": True}
    if sensitivity in BLOCKED_SENSITIVITIES:
        for field in ("text", "normalized_text", "content", "value"):
            if field in safe:
                safe[field] = "[REDACTED]"
        safe["redacted"] = True
        safe["redaction_reason"] = f"sensitivity:{sensitivity}"
    return safe
