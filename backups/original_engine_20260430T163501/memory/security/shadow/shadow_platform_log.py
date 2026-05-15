from __future__ import annotations

import json
import subprocess
from typing import Any, Dict


def emit_system_log(event: str, payload: Dict[str, Any]) -> None:
    msg = {"event": str(event or ""), **dict(payload or {})}
    text = json.dumps(msg, ensure_ascii=False)[:3500]
    try:
        subprocess.run(
            ["logger", "-t", "openclaw-shadow", text],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return

