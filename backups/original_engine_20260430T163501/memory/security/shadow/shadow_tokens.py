from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Dict, Iterable, Set


class ShadowTokenManager:
    """Lightweight capability token manager (local process scope)."""

    def __init__(self) -> None:
        self._secret = os.urandom(32)
        self._registry: Dict[str, Dict[str, object]] = {}

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
        if now >= int(row.get("exp", 0) or 0):
            self._registry.pop(str(token), None)
            return False
        if str(row.get("caller_id", "")) != str(caller_id):
            return False
        perms: Set[str] = set(row.get("perms", set()))  # type: ignore[arg-type]
        return str(required_permission) in perms

