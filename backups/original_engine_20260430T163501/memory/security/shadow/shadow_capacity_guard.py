from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


class ShadowCapacityGuard:
    """Capacity/watermark guard for shadow_data and payloads."""

    def __init__(
        self,
        shadow_dir: Path,
        *,
        shadow_max_mb: float = 512.0,
        payload_max_mb: float = 256.0,
        enter_pct: float = 0.95,
        exit_pct: float = 0.80,
        warn_pct: float = 0.70,
        alert_pct: float = 0.85,
    ) -> None:
        self.shadow_dir = Path(shadow_dir)
        self.shadow_max_bytes = max(1.0, float(shadow_max_mb)) * 1024 * 1024
        self.payload_max_bytes = max(1.0, float(payload_max_mb)) * 1024 * 1024
        self.enter_pct = float(enter_pct)
        self.exit_pct = float(exit_pct)
        self.warn_pct = float(warn_pct)
        self.alert_pct = float(alert_pct)

    def usage(self) -> Dict[str, int]:
        total = 0
        payload = 0
        for p in self.shadow_dir.rglob("*"):
            try:
                if not p.is_file():
                    continue
                sz = int(p.stat().st_size)
                total += sz
                if "payloads" in p.parts:
                    payload += sz
            except Exception:
                continue
        return {"shadow_total": total, "payload_total": payload}

    def evaluate(self) -> Dict[str, Any]:
        use = self.usage()
        shadow_ratio = float(use["shadow_total"]) / float(self.shadow_max_bytes)
        payload_ratio = float(use["payload_total"]) / float(self.payload_max_bytes)
        ratio = max(shadow_ratio, payload_ratio)
        stage = "ok"
        if ratio >= self.enter_pct:
            stage = "critical"
        elif ratio >= self.alert_pct:
            stage = "alert"
        elif ratio >= self.warn_pct:
            stage = "warning"
        return {
            "ratio": ratio,
            "stage": stage,
            "usage": use,
            "limits": {
                "shadow_max_bytes": int(self.shadow_max_bytes),
                "payload_max_bytes": int(self.payload_max_bytes),
            },
            "enter_pct": self.enter_pct,
            "exit_pct": self.exit_pct,
        }

