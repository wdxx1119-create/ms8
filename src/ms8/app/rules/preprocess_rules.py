from __future__ import annotations

import re


def preprocess_text(text: str) -> str:
    # Normalize common full-width punctuation into ASCII for downstream rules.
    fullwidth_map = str.maketrans(
        {
            "，": ",",
            "。": ".",
            "！": "!",
            "？": "?",
            "：": ":",
            "；": ";",
            "（": "(",
            "）": ")",
            "【": "[",
            "】": "]",
            "“": '"',
            "”": '"',
            "‘": "'",
            "’": "'",
            "　": " ",
        }
    )
    text = str(text or "").translate(fullwidth_map)
    text = re.sub(r"\r\n?", "\n", text)
    # Trim quoted-message style headers.
    text = re.sub(
        r"^\s*(?:>+\s*)?(?:On\s.+?wrote:|From:\s.+|Sent:\s.+|Subject:\s.+)\s*$",
        " ",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    text = re.sub(r"```[\s\S]*?```", " [CODE_BLOCK] ", text)
    text = re.sub(r"`[^`]+`", " [INLINE_CODE] ", text)
    # Compress noisy repeated chars: 啊啊啊啊 -> 啊啊
    text = re.sub(r"([\u4e00-\u9fffA-Za-z0-9])\1{2,}", r"\1\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
