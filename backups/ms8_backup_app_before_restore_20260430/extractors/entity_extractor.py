from __future__ import annotations

import re


def extract_entities(text: str) -> list[str]:
    words = re.findall(r"\b[A-Za-z][A-Za-z0-9_\-]{2,}\b", text)
    uniq: list[str] = []
    for w in words:
        if w.lower() in {"the", "and", "for", "with", "from", "that", "this"}:
            continue
        if w not in uniq:
            uniq.append(w)
    return uniq[:12]
