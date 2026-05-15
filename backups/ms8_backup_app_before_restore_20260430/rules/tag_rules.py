from __future__ import annotations


def derive_aux_tags(category: str, signals: dict) -> list[str]:
    tags = [category]
    if signals.get("code_blocks", 0) > 0:
        tags.append("code")
    if signals.get("links"):
        tags.append("link")
    if signals.get("files"):
        tags.append("file")
    return sorted(set(tags))
