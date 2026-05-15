from __future__ import annotations

import hashlib
import re

TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]")


def make_dedupe_key(category: str, normalized_text: str) -> str:
    payload = f"{category}:{normalized_text}".encode()
    return hashlib.sha256(payload).hexdigest()[:20]


def text_tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in TOKEN_RE.finditer(text or "")}


def jaccard_similarity(a: str, b: str) -> float:
    ta = text_tokens(a)
    tb = text_tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / max(1, union)
