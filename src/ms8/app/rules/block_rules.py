from __future__ import annotations

import re
import unicodedata

BLOCK_PATTERNS = [r"^\s*$", r"^(ok|好的|收到|嗯嗯|thanks?)$"]
LOW_VALUE_COMMANDS = {"继续", "看看", "下一个", "展开"}
SEMANTIC_SHORT_ALLOWLIST = {
    "改成",
    "启用",
    "禁用",
    "方案",
    "缓存",
    "sqlite",
    "mysql",
    "postgres",
    "redis",
    "回滚",
    "发布",
    "阈值",
}
SYSTEM_PLACEHOLDERS = {"[图片]", "[语音]", "[文件]", "[链接]"}
PUNCTUATION_CHARS = r"[\s\.,!?;:，。！？；：、~`'\"“”‘’()\[\]{}<>《》…—\-_/\\|]+"


def _is_emoji_only(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    emoji_like = 0
    total = 0
    for ch in stripped:
        if ch.isspace():
            continue
        total += 1
        cp = ord(ch)
        if 0x1F300 <= cp <= 0x1FAFF:
            emoji_like += 1
        elif unicodedata.category(ch) in {"So", "Sk"}:
            emoji_like += 1
    return total > 0 and emoji_like / total >= 0.8


def _normalize_for_match(text: str) -> str:
    payload = str(text or "").strip().lower()
    payload = re.sub(r"\s+", "", payload)
    payload = re.sub(PUNCTUATION_CHARS, "", payload)
    return payload


def _emoji_ratio(text: str) -> float:
    stripped = str(text or "").strip()
    if not stripped:
        return 0.0
    emoji_like = 0
    total = 0
    for ch in stripped:
        if ch.isspace():
            continue
        total += 1
        cp = ord(ch)
        if 0x1F300 <= cp <= 0x1FAFF or unicodedata.category(ch) in {"So", "Sk"}:
            emoji_like += 1
    return (emoji_like / total) if total else 0.0


def _is_repeated_noise(text: str) -> bool:
    payload = str(text or "")
    compact = re.sub(r"\s+", "", payload)
    if len(compact) < 3:
        return False
    if re.search(r"(.)\1\1", compact):
        unique = len(set(compact))
        if unique <= 3:
            return True
    return False


def evaluate_block(text: str) -> dict[str, str | bool]:
    stripped = str(text or "").strip()
    normalized = _normalize_for_match(stripped)
    if not stripped:
        return {
            "blocked": True,
            "reason": "empty_or_whitespace",
            "severity": "high",
            "suggested_route": "rejected",
        }
    if stripped in SYSTEM_PLACEHOLDERS:
        return {
            "blocked": True,
            "reason": "system_placeholder",
            "severity": "high",
            "suggested_route": "rejected",
        }
    if re.fullmatch(r"\d+", stripped):
        return {
            "blocked": True,
            "reason": "numeric_only",
            "severity": "high",
            "suggested_route": "rejected",
        }
    if re.fullmatch(r"[a-z]+\d+|\d+[a-z]+", normalized, flags=re.IGNORECASE) and len(normalized) <= 10:
        return {
            "blocked": True,
            "reason": "alnum_noise",
            "severity": "medium",
            "suggested_route": "rejected",
        }
    if re.fullmatch(r"[^\w\s]+", stripped):
        return {
            "blocked": True,
            "reason": "punctuation_only",
            "severity": "high",
            "suggested_route": "rejected",
        }
    if _is_emoji_only(stripped):
        return {
            "blocked": True,
            "reason": "emoji_only",
            "severity": "high",
            "suggested_route": "rejected",
        }
    emoji_ratio = _emoji_ratio(stripped)
    non_emoji_compact = re.sub(r"[\U0001F300-\U0001FAFF]", "", stripped)
    non_emoji_compact = re.sub(PUNCTUATION_CHARS, "", non_emoji_compact)
    if emoji_ratio >= 0.5 and len(non_emoji_compact) <= 1:
        return {
            "blocked": True,
            "reason": "emoji_mixed_low_value",
            "severity": "medium",
            "suggested_route": "rejected",
        }
    if _is_repeated_noise(stripped):
        return {
            "blocked": True,
            "reason": "repeated_chars_noise",
            "severity": "medium",
            "suggested_route": "rejected",
        }
    for pattern in BLOCK_PATTERNS:
        if re.search(pattern, stripped, flags=re.IGNORECASE) or re.search(pattern, normalized, flags=re.IGNORECASE):
            return {
                "blocked": True,
                "reason": "template_ack",
                "severity": "medium",
                "suggested_route": "rejected",
            }
    compact = re.sub(PUNCTUATION_CHARS, "", stripped.lower())
    if any(compact == cmd or compact.startswith(cmd) or compact.endswith(cmd) for cmd in LOW_VALUE_COMMANDS):
        if not any(tok in compact for tok in SEMANTIC_SHORT_ALLOWLIST):
            return {
                "blocked": True,
                "reason": "short_low_value_command",
                "severity": "low",
                "suggested_route": "short_term_only",
            }
    if normalized in {"ok", "okay", "好的", "收到", "谢谢", "thanks"}:
        return {
            "blocked": True,
            "reason": "template_ack",
            "severity": "medium",
            "suggested_route": "rejected",
        }
    if stripped in LOW_VALUE_COMMANDS and not any(tok in stripped.lower() for tok in SEMANTIC_SHORT_ALLOWLIST):
        return {
            "blocked": True,
            "reason": "short_low_value_command",
            "severity": "low",
            "suggested_route": "short_term_only",
        }
    return {"blocked": False, "reason": "", "severity": "none", "suggested_route": "accepted"}


def should_block(text: str) -> tuple[bool, str]:
    result = evaluate_block(text)
    return bool(result["blocked"]), str(result["reason"])
