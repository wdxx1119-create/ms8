from __future__ import annotations

from typing import Any, Dict

from .shadow_ledger import ShadowLedger


class ShadowCheckpointGuard:
    """Checkpoint verification and truncation detection gate."""

    def __init__(self, ledger: ShadowLedger) -> None:
        self.ledger = ledger

    def detect_ledger_truncation(self) -> Dict[str, Any]:
        events = list(self.ledger.read_events())
        max_seq = 0
        for e in events:
            try:
                max_seq = max(max_seq, int(e.get("seq", 0) or 0))
            except Exception:
                continue
        last_cp_seq = 0
        if self.ledger.checkpoints_file.exists():
            try:
                import json

                for line in self.ledger.checkpoints_file.read_text(encoding="utf-8").splitlines():
                    raw = line.strip()
                    if not raw:
                        continue
                    obj = json.loads(raw)
                    last_cp_seq = max(last_cp_seq, int(obj.get("upto_seq", 0) or 0))
            except Exception:
                pass
        truncated = last_cp_seq > max_seq and max_seq > 0
        return {"truncated": truncated, "max_seq": max_seq, "last_checkpoint_seq": last_cp_seq}

    def verify_gate(self) -> Dict[str, Any]:
        verify = self.ledger.verify_checkpoints()
        trunc = self.detect_ledger_truncation()
        ok = bool(verify.get("ok", False)) and (not bool(trunc.get("truncated", False)))
        reason = "ok"
        if not bool(verify.get("ok", False)):
            reason = "checkpoint_mismatch"
        elif bool(trunc.get("truncated", False)):
            reason = "ledger_truncated_detected"
        return {
            "ok": ok,
            "reason": reason,
            "verify": verify,
            "truncation": trunc,
        }

