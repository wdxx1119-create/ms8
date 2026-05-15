from __future__ import annotations

import re

BLOCK_PATTERNS = [
    r"^\s*$",
    r"^(ok|好的|收到|嗯嗯|thanks?)$",
]


def should_block(text: str) -> tuple[bool, str]:
    for pattern in BLOCK_PATTERNS:
        if re.search(pattern, text.strip(), flags=re.IGNORECASE):
            return True, f"blocked_by:{pattern}"
    return False, ""
