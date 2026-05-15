from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, Optional


@dataclass
class Lease:
    op_name: str
    lease_id: str
    owner: str
    expires_at: float

    def expired(self) -> bool:
        return time.monotonic() >= float(self.expires_at)


class ShadowLocking:
    """
    Serialize high-risk operations with lease timeout and fixed lock order.
    Lock order (fixed): state -> control -> replay -> recover -> checkpoint -> backup
    """

    LOCK_ORDER = ("state", "control", "replay", "recover", "checkpoint", "backup")

    def __init__(self) -> None:
        self._mux = threading.RLock()
        self._active: Optional[Lease] = None
        self._cooldowns: Dict[str, float] = {}

    def _can_run_after_cooldown(self, op_name: str, cooldown_s: int) -> bool:
        if cooldown_s <= 0:
            return True
        now = time.monotonic()
        next_ok = float(self._cooldowns.get(op_name, 0.0) or 0.0)
        return now >= next_ok

    def _set_cooldown(self, op_name: str, cooldown_s: int) -> None:
        if cooldown_s <= 0:
            return
        self._cooldowns[op_name] = time.monotonic() + max(1, int(cooldown_s))

    def current_lease(self) -> Optional[Lease]:
        with self._mux:
            if self._active and self._active.expired():
                self._active = None
            return self._active

    def validate_lease(self, lease_id: str) -> bool:
        with self._mux:
            cur = self.current_lease()
            return bool(cur and str(cur.lease_id) == str(lease_id))

    @contextmanager
    def acquire(self, op_name: str, owner: str, *, ttl_s: int = 120, cooldown_s: int = 0) -> Iterator[Lease]:
        with self._mux:
            cur = self.current_lease()
            if cur is not None:
                raise RuntimeError(f"operation_locked:{cur.op_name}:{cur.owner}")
            if not self._can_run_after_cooldown(op_name, cooldown_s):
                raise RuntimeError(f"operation_cooldown:{op_name}")
            lease = Lease(
                op_name=str(op_name),
                lease_id=f"lease-{uuid.uuid4().hex[:10]}",
                owner=str(owner or "unknown"),
                expires_at=time.monotonic() + max(5, int(ttl_s)),
            )
            self._active = lease
        try:
            yield lease
        finally:
            with self._mux:
                cur2 = self.current_lease()
                if cur2 and cur2.lease_id == lease.lease_id:
                    self._active = None
                    self._set_cooldown(op_name, cooldown_s)
