from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ShadowEvent:
    event_id: str
    seq: int
    ts: str
    event_type: str  # data | mode | protection
    action: str  # write/read/delete/update/seal/unseal/recover/protect/checkpoint
    source: str
    mode: str  # active | sealed | recovering | minimal_survival
    ok: bool
    error: str = ""
    content_hash: str = ""
    summary: str = ""
    payload_file: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    checkpoint_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "seq": self.seq,
            "ts": self.ts,
            "event_type": self.event_type,
            "action": self.action,
            "source": self.source,
            "mode": self.mode,
            "ok": self.ok,
            "error": self.error,
            "content_hash": self.content_hash,
            "summary": self.summary,
            "payload_file": self.payload_file,
            "metadata": self.metadata,
            "checkpoint_hash": self.checkpoint_hash,
        }


@dataclass
class SealManifest:
    sealed: bool = False
    seal_level: str = "hard"  # soft | hard
    mode: str = "active"  # active | sealed | recovering | minimal_survival
    sealed_at: str = ""
    reason: str = ""
    seal_session_id: str = ""
    sealed_write_count: int = 0
    write_error_streak: int = 0
    last_recovered_at: str = ""
    minimal_survival_reason: str = ""
    history: list[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sealed": self.sealed,
            "seal_level": self.seal_level,
            "mode": self.mode,
            "sealed_at": self.sealed_at,
            "reason": self.reason,
            "seal_session_id": self.seal_session_id,
            "sealed_write_count": int(self.sealed_write_count),
            "write_error_streak": int(self.write_error_streak),
            "last_recovered_at": self.last_recovered_at,
            "minimal_survival_reason": self.minimal_survival_reason,
            "history": list(self.history),
        }


@dataclass
class SpoolItem:
    spool_id: str
    ts: str
    source: str
    content_hash: str
    content: str
    replayed: bool = False
    replay_attempts: int = 0
    last_error: str = ""
    replayed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spool_id": self.spool_id,
            "ts": self.ts,
            "source": self.source,
            "content_hash": self.content_hash,
            "content": self.content,
            "replayed": bool(self.replayed),
            "replay_attempts": int(self.replay_attempts),
            "last_error": self.last_error,
            "replayed_at": self.replayed_at,
        }
