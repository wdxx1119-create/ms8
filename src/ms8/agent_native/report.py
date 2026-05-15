"""Structured report helpers for agent-native."""

from __future__ import annotations


def block(name: str, payload: dict) -> str:
    lines = [name]
    for k, v in payload.items():
        if isinstance(v, list):
            lines.append(f"{k}=[")
            for item in v:
                lines.append(f"  {item}")
            lines.append("]")
        else:
            lines.append(f"{k}={v}")
    return "\n".join(lines)
