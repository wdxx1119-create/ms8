"""Text chunking helpers for absorb."""

from __future__ import annotations

import hashlib
import re


def estimate_tokens(text: str) -> int:
    value = str(text or "")
    cjk = len(re.findall(r"[\u4e00-\u9fff]", value))
    words = len(re.findall(r"[A-Za-z0-9_]+", value))
    other = max(0, len(value) - cjk - sum(len(m.group(0)) for m in re.finditer(r"[A-Za-z0-9_]+", value)))
    return max(1, cjk + words + other // 4)


def make_chunk_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _slice_by_chars(text: str, max_tokens: int) -> int:
    # Approximate mixed-language token/character ratio conservatively.
    return max(1, max_tokens * 3)


def split_text(text: str, max_tokens: int = 512, overlap_tokens: int = 64) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    if estimate_tokens(value) <= max_tokens:
        return [value]
    char_window = _slice_by_chars(value, max_tokens)
    char_overlap = _slice_by_chars(value, overlap_tokens)
    chunks: list[str] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + char_window)
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(value):
            break
        start = max(0, end - char_overlap)
    return chunks
