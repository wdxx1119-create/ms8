from __future__ import annotations

import re

PII_PATTERNS = {
    "email": r"\b[\w\.-]+@[\w\.-]+\.\w+\b",
    "phone": r"\b(?:\+?\d{1,3}[- ]?)?(?:1[3-9]\d{9}|\d{3}[- ]?\d{3}[- ]?\d{4})\b",
    "token": r"\b(?:sk-[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{16})\b",
    "github_token": r"\b(?:ghp_\s*[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
    "openai_project_key": r"\bsk-proj-[A-Za-z0-9_-]{10,}\b",
    "jwt": r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
    "bearer_token": r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*\b",
    "ssh_private_key": (
        r"-----BEGIN (?:RSA |OPENSSH |EC |ENCRYPTED )?PRIVATE KEY-----[\s\S]+?"
        r"-----END (?:RSA |OPENSSH |EC |ENCRYPTED )?PRIVATE KEY-----"
    ),
    "db_connection": r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s]+",
    "webhook_url": r"https?://[^\s]*?(?:webhook|callback|hook)[^\s]*",
    "internal_ip_port": (
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|"
        r"172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})(?::\d{2,5})\b"
    ),
    "password_field": r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*[^\s,;]+",
    "password_cn": r"(?:密码|口令)\s*[:：=]\s*[^\s,;，。]+",
}


def _mask_match(name: str, value: str) -> str:
    if name == "email":
        return "[REDACTED_EMAIL]"
    if name == "phone":
        return "[REDACTED_PHONE]"
    if name in {"token", "github_token", "openai_project_key", "jwt", "bearer_token"}:
        return "[REDACTED_TOKEN]"
    if name == "ssh_private_key":
        return "[REDACTED_PRIVATE_KEY]"
    if name == "db_connection":
        return "[REDACTED_DB_URI]"
    if name == "webhook_url":
        return "[REDACTED_WEBHOOK]"
    if name == "internal_ip_port":
        return "[REDACTED_INTERNAL_ENDPOINT]"
    if name in {"password_field", "password_cn"}:
        return "[REDACTED_PASSWORD]"
    return "[REDACTED]"


def detect_sensitive_segments(text: str) -> dict[str, list[dict[str, str]]]:
    segments: dict[str, list[dict[str, str]]] = {}
    payload = str(text or "")
    for name, pattern in PII_PATTERNS.items():
        hits = []
        for match in re.finditer(pattern, payload, flags=re.IGNORECASE):
            hits.append({"value": match.group(0), "start": str(match.start()), "end": str(match.end())})
        if hits:
            segments[name] = hits
    return segments


def redact_sensitive_text(text: str) -> dict[str, object]:
    payload = str(text or "")
    segments = detect_sensitive_segments(payload)
    redacted = payload
    for name, hits in segments.items():
        for hit in hits:
            value = str(hit["value"])
            redacted = redacted.replace(value, _mask_match(name, value))
    flags = sorted(segments.keys())
    severity = "none"
    suggested_route = "accepted"
    if any(x in flags for x in {"ssh_private_key", "password_field"}):
        severity = "critical"
        suggested_route = "pending_review"
    elif flags:
        severity = "high"
        suggested_route = "redacted_accept"
    return {
        "has_sensitive": bool(flags),
        "flags": flags,
        "redacted_text": redacted,
        "severity": severity,
        "suggested_route": suggested_route,
    }


def privacy_check(text: str) -> tuple[bool, list[str]]:
    result = redact_sensitive_text(text)
    raw_flags = result.get("flags", [])
    flags = list(raw_flags) if isinstance(raw_flags, list) else []
    return (not bool(result.get("has_sensitive", False)), flags)


def scan_memory_md_sensitive(text: str) -> dict[str, object]:
    result = redact_sensitive_text(text)
    return {
        "has_sensitive": result["has_sensitive"],
        "flags": result["flags"],
        "suggested_route": result["suggested_route"],
    }
