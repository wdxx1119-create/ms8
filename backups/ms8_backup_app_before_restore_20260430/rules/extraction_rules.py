from __future__ import annotations

import re


def extract_signals(text: str) -> dict:
    code_blocks = len(re.findall(r"\[CODE_BLOCK\]|```[\s\S]*?```", text))
    links = re.findall(r"https?://\S+", text)
    files = re.findall(r"\b[\w./-]+\.(?:py|js|ts|md|yaml|yml|json|toml|sql|sh)\b", text)
    return {
        "code_blocks": code_blocks,
        "links": links,
        "files": files,
    }
