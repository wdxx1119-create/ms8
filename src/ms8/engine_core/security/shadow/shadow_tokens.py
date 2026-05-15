from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Iterable


class ShadowTokenManager:
    """Lightweight capability token manager (local process scope)."""

    def __init__(self) -> None:
        self._secret = os.urandom(32)
        self._registry: dict[str, dict[str, object]] = {}

    def issue_token(self, caller_id: str, permissions: Iterable[str], ttl_seconds: int = 600) -> str:
        now = int(time.time())
        exp = now + max(30, int(ttl_seconds))
        perms = sorted({str(x).strip() for x in permissions if str(x).strip()})
        body = f"{caller_id}|{exp}|{','.join(perms)}"
        sig = hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).hexdigest()
        token = f"shd.{caller_id}.{exp}.{sig[:24]}"
        self._registry[token] = {
            "caller_id": str(caller_id),
            "exp": exp,
            "perms": set(perms),
        }
        return token

    def revoke_token(self, token: str) -> None:
        self._registry.pop(str(token), None)

    def validate_token(self, token: str, required_permission: str, caller_id: str) -> bool:
        row = self._registry.get(str(token))
        if not row:
            return False
        now = int(time.time())
        exp_raw = row.get("exp", 0)
        exp = int(exp_raw) if isinstance(exp_raw, (int, float, str)) else 0
        if now >= exp:
            self._registry.pop(str(token), None)
            return False
        if str(row.get("caller_id", "")) != str(caller_id):
            return False
        perms_raw = row.get("perms", set())
        if isinstance(perms_raw, set):
            perms: set[str] = {str(p) for p in perms_raw}
        elif isinstance(perms_raw, Iterable):
            perms = {str(p) for p in perms_raw}
        else:
            perms = set()
        return str(required_permission) in perms
