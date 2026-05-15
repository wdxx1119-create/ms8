from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def _path_exists(path: str) -> bool:
    return Path(path).exists() if path else False


def scan_mcp_tools() -> dict[str, Any]:
    return scan_local_tools()


def scan_local_tools() -> dict[str, Any]:
    tools = {
        "python": shutil.which("python3") or "",
        "ollama": shutil.which("ollama") or "",
        "git": shutil.which("git") or "",
    }
    available = {k: bool(v) and _path_exists(v) for k, v in tools.items()}
    return {"ok": True, "tools": tools, "available": available}
