from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .shadow_fs_guard import set_immutable, set_mutable
from .shadow_schema import utc_now_iso


class ShadowAudit:
    """High-risk control-plane audit log writer."""

    def __init__(self, shadow_dir: Path, *, immutable_enabled: bool = False) -> None:
        self.shadow_dir = shadow_dir
        self.audit_file = self.shadow_dir / "ops_audit.jsonl"
        self.immutable_enabled = bool(immutable_enabled)
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.audit_file.exists():
            self.audit_file.write_text("", encoding="utf-8")
        # Safety-heal: if immutable is disabled by config but file still has uchg
        # from previous runs, clear it so audit remains writable.
        if not self.immutable_enabled:
            set_mutable(self.audit_file, enabled=True)

    def append(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        row = {
            "ts": utc_now_iso(),
            **dict(payload or {}),
        }
        # Always attempt to clear immutable before append to avoid "stuck readonly"
        # when runtime config changed from immutable=true -> false.
        set_mutable(self.audit_file, enabled=True)
        try:
            with self.audit_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        finally:
            set_immutable(self.audit_file, enabled=self.immutable_enabled)
        return row
