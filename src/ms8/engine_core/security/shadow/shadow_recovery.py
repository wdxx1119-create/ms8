from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from .shadow_ledger import ShadowLedger
from .shadow_recovery_guard import RecoveryRecord, ShadowRecoveryGuard
from .shadow_schema import utc_now_iso
from .shadow_seal import ShadowSeal

WriteFunc = Callable[[str, str, dict[str, Any]], Any]
HashExistsFunc = Callable[[str], bool]


class ShadowRecovery:
    def __init__(
        self,
        ledger: ShadowLedger,
        seal: ShadowSeal,
        guard: ShadowRecoveryGuard,
    ) -> None:
        self.ledger = ledger
        self.seal = seal
        self.guard = guard
        self._targets: dict[str, WriteFunc] = {}
        self._hash_exists: dict[str, HashExistsFunc] = {}

    def bind_target(self, target: str, write_func: WriteFunc, hash_exists_func: HashExistsFunc | None = None) -> None:
        self._targets[str(target)] = write_func
        if hash_exists_func:
            self._hash_exists[str(target)] = hash_exists_func

    def replay_spool(
        self,
        target: str = "main_memory",
    ) -> dict[str, Any]:
        if target not in self.guard.TARGETS:
            return {"status": "rejected", "reason": "invalid_recovery_target"}
        writer = self._targets.get(target)
        if writer is None:
            return {"status": "rejected", "reason": "target_not_bound", "target": target}
        hash_exists_func = self._hash_exists.get(target)
        rows = self.ledger.read_spool()
        if not rows:
            return {"status": "success", "total": 0, "replayed": 0, "skipped": 0, "failed": 0}

        self.seal.mark_recovering()
        batch_id = f"replay-{uuid.uuid4().hex[:10]}"
        scan_rows = self.guard.scan(since_ts="", include_spool=True)
        spool_pending = [r for r in scan_rows if r.origin == "spool"]
        admit, quarantine, skipped_rows = self.guard.decide(spool_pending, target=target)
        apply = self.guard.apply(
            batch_id=batch_id,
            rows=admit,
            write_target=writer,
            hash_exists_func=hash_exists_func,
            allow_source_prefix="shadow:",
        )
        if quarantine:
            self.guard.quarantine(quarantine, reason="replay_decide_quarantine")

        # Mark spool rows based on decision/apply outcome.
        by_id: dict[str, RecoveryRecord] = {r.event_id: r for r in admit + quarantine + skipped_rows}
        for row in rows:
            if bool(row.get("replayed", False)):
                continue
            rid = str(row.get("spool_id", ""))
            dec = by_id.get(rid)
            if dec is None:
                continue
            row["replay_attempts"] = int(row.get("replay_attempts", 0) or 0) + 1
            row["replay_batch_id"] = batch_id
            if dec.replay_state in {"replayed", "skip", "quarantine"}:
                row["replayed"] = True
                row["replayed_at"] = utc_now_iso()
                row["last_error"] = str(dec.failure_reason or "")
            else:
                row["replayed"] = False
                row["last_error"] = str(dec.failure_reason or "replay_failed")

        self.ledger.rewrite_spool(rows)
        if int(apply.get("failed", 0) or 0) == 0:
            self.seal.clear_seal(reason="replay_spool_success")
        else:
            self.seal.trigger_seal(
                reason=f"replay_partial_failed:{int(apply.get('failed', 0) or 0)}",
                level=self.seal.seal_level(),
            )

        return {
            "status": "success" if int(apply.get("failed", 0) or 0) == 0 else "partial",
            "batch_id": batch_id,
            "total": len(rows),
            "replayed": int(apply.get("replayed", 0) or 0),
            "skipped": int(apply.get("skipped", 0) or 0) + len(skipped_rows),
            "failed": int(apply.get("failed", 0) or 0),
            "quarantined": len(quarantine),
            "remaining": sum(1 for r in rows if not bool(r.get("replayed", False))),
        }

    def recover_from_events(
        self,
        target: str = "main_memory",
        since_ts: str | None = None,
    ) -> dict[str, Any]:
        if target not in self.guard.TARGETS:
            return {"status": "rejected", "reason": "invalid_recovery_target"}
        writer = self._targets.get(target)
        if writer is None:
            return {"status": "rejected", "reason": "target_not_bound", "target": target}
        hash_exists_func = self._hash_exists.get(target)
        scanned = self.guard.scan(since_ts=str(since_ts or ""), include_spool=False)
        if not scanned:
            return {"status": "success", "total": 0, "recovered": 0, "skipped": 0, "failed": 0}

        self.seal.mark_recovering()
        batch_id = f"recover-{uuid.uuid4().hex[:10]}"
        admit, quarantine, skipped_rows = self.guard.decide(scanned, target=target)
        apply = self.guard.apply(
            batch_id=batch_id,
            rows=admit,
            write_target=writer,
            hash_exists_func=hash_exists_func,
            allow_source_prefix="shadow:",
        )
        if quarantine:
            self.guard.quarantine(quarantine, reason="recover_decide_quarantine")

        if int(apply.get("failed", 0) or 0) == 0:
            self.seal.clear_seal(reason="recover_from_events_success")
        else:
            self.seal.trigger_seal(
                reason=f"recover_from_events_partial_failed:{int(apply.get('failed', 0) or 0)}",
                level=self.seal.seal_level(),
            )
        return {
            "status": "success" if int(apply.get("failed", 0) or 0) == 0 else "partial",
            "batch_id": batch_id,
            "total": len(scanned),
            "recovered": int(apply.get("replayed", 0) or 0),
            "skipped": int(apply.get("skipped", 0) or 0) + len(skipped_rows),
            "failed": int(apply.get("failed", 0) or 0),
            "quarantined": len(quarantine),
        }
