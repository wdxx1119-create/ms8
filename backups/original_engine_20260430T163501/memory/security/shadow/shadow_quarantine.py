from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .shadow_schema import utc_now_iso


def grade_reason(reason: str) -> str:
    r = str(reason or "").lower()
    if "signature" in r or "tamper" in r or "invalid" in r:
        return "high"
    if "admission" in r or "payload_too_large" in r:
        return "medium"
    return "low"


class ShadowQuarantine:
    def __init__(self, shadow_dir: Path) -> None:
        self.shadow_dir = shadow_dir
        self.dir = self.shadow_dir / "quarantine"
        self.dir.mkdir(parents=True, exist_ok=True)

    def append(self, row: Dict, reason: str) -> Dict:
        sev = grade_reason(reason)
        stamp = utc_now_iso().replace(":", "").replace("-", "")
        out_file = self.dir / f"quarantine_{sev}_{stamp}.jsonl"
        payload = dict(row or {})
        payload["quarantine_reason"] = str(reason or "")
        payload["quarantine_severity"] = sev
        with out_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return {"status": "success", "severity": sev, "file": str(out_file)}

