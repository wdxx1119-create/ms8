from __future__ import annotations

import re


def preprocess_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"```[\s\S]*?```", " [CODE_BLOCK] ", text)
    text = re.sub(r"`[^`]+`", " [INLINE_CODE] ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
