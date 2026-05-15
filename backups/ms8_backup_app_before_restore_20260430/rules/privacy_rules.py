from __future__ import annotations

import re

PII_PATTERNS = {
    "email": r"\b[\w\.-]+@[\w\.-]+\.\w+\b",
    "phone": r"\b(?:\+?\d{1,3}[- ]?)?(?:1[3-9]\d{9}|\d{3}[- ]?\d{3}[- ]?\d{4})\b",
    "token": r"\b(?:sk-[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{16})\b",
}


def privacy_check(text: str) -> tuple[bool, list[str]]:
    hits: list[str] = []
    for name, pattern in PII_PATTERNS.items():
        if re.search(pattern, text):
            hits.append(name)
    return (len(hits) == 0, hits)
