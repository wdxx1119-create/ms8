from __future__ import annotations

import hashlib


def make_dedupe_key(category: str, normalized_text: str) -> str:
    payload = f"{category}:{normalized_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]
