from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .shadow_ledger import ShadowLedger
from .shadow_quarantine import ShadowQuarantine
from .shadow_schema import utc_now_iso

logger = logging.getLogger(__name__)


def _parse_ts(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


@dataclass
class RecoveryRecord:
    event_id: str
    source: str
    text: str
    content_hash: str
    ts: str
    mode: str
    origin: str  # events | spool
    replay_state: str = "pending"
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "source": self.source,
            "text": self.text,
            "content_hash": self.content_hash,
            "ts": self.ts,
            "mode": self.mode,
            "origin": self.origin,
            "replay_state": self.replay_state,
            "failure_reason": self.failure_reason,
        }


class ShadowRecoveryGuard:
    """
    Three-stage recovery pipeline:
    1) scan
    2) decide route (admit/quarantine/skip)
    3) apply batch replay
    """

    TARGETS = {"main_memory", "quarantine_memory", "drill_memory"}

    def __init__(
        self,
        ledger: ShadowLedger,
        shadow_dir: Path,
        *,
        admission_check: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.ledger = ledger
        self.shadow_dir = shadow_dir
        self.admission_check = admission_check
        self.batch_journal = self.shadow_dir / "recovery_batches.jsonl"
        self.batch_journal.parent.mkdir(parents=True, exist_ok=True)
        if not self.batch_journal.exists():
            self.batch_journal.write_text("", encoding="utf-8")
        self.quarantine_store = ShadowQuarantine(self.shadow_dir)

    def _load_payload_text(self, event: dict[str, Any]) -> str:
        payload = str(event.get("summary", ""))
        payload_file = str(event.get("payload_file", ""))
        if not payload_file:
            return payload
        pf = self.ledger.payload_dir / payload_file
        if not pf.exists():
            return payload
        try:
            obj = json.loads(pf.read_text(encoding="utf-8"))
            return str(obj.get("content", payload))
        except (json.JSONDecodeError, OSError):
            return payload

    def scan(self, since_ts: str = "", include_spool: bool = True) -> list[RecoveryRecord]:
        since_dt = _parse_ts(since_ts)
        out: list[RecoveryRecord] = []
        seen_ids = set()

        for e in self.ledger.read_events():
            if str(e.get("event_type", "")) != "data":
                continue
            if str(e.get("action", "")) != "write":
                continue
            if str(e.get("mode", "")) != "sealed":
                continue
            ts_text = str(e.get("ts", ""))
            ts = _parse_ts(ts_text)
            if since_dt and (ts is None or ts < since_dt):
                continue
            event_id = str(e.get("event_id", "")).strip()
            if not event_id or event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            text = self._load_payload_text(e)
            out.append(
                RecoveryRecord(
                    event_id=event_id,
                    source=str(e.get("source", "shadow:recovery")),
                    text=text,
                    content_hash=str(e.get("content_hash", "")),
                    ts=ts_text,
                    mode=str(e.get("mode", "sealed")),
                    origin="events",
                )
            )

        if include_spool:
            for row in self.ledger.read_spool():
                if bool(row.get("replayed", False)):
                    continue
                event_id = str(row.get("spool_id", "")).strip()
                if not event_id or event_id in seen_ids:
                    continue
                seen_ids.add(event_id)
                out.append(
                    RecoveryRecord(
                        event_id=event_id,
                        source=str(row.get("source", "shadow:spool")),
                        text=str(row.get("content", "")),
                        content_hash=str(row.get("content_hash", "")),
                        ts=str(row.get("ts", "")),
                        mode="sealed",
                        origin="spool",
                    )
                )
        return out

    def decide(
        self, rows: Iterable[RecoveryRecord], target: str
    ) -> tuple[list[RecoveryRecord], list[RecoveryRecord], list[RecoveryRecord]]:
        if target not in self.TARGETS:
            raise ValueError("invalid_recovery_target")
        admit: list[RecoveryRecord] = []
        quarantine: list[RecoveryRecord] = []
        skip: list[RecoveryRecord] = []
        seen_hash = set()
        for rec in rows:
            if not rec.text.strip():
                rec.replay_state = "skip"
                rec.failure_reason = "empty_payload"
                skip.append(rec)
                continue
            if len(rec.text.encode("utf-8", errors="ignore")) > 1024 * 100:
                rec.replay_state = "quarantine"
                rec.failure_reason = "payload_too_large"
                quarantine.append(rec)
                continue
            if rec.content_hash and rec.content_hash in seen_hash:
                rec.replay_state = "skip"
                rec.failure_reason = "duplicate_in_batch"
                skip.append(rec)
                continue
            if rec.content_hash:
                seen_hash.add(rec.content_hash)
            if self.admission_check:
                decision = self.admission_check(rec.text, {"source": "shadow_recovery", "origin": rec.origin})
                route = "accepted"
                if isinstance(decision, dict):
                    route = str((decision or {}).get("route", "accepted"))
                elif decision is not None:
                    raw_route = getattr(decision, "route", "accepted")
                    route = str(getattr(raw_route, "value", raw_route))
                if route in {"rejected", "pending_review"}:
                    rec.replay_state = "quarantine"
                    rec.failure_reason = f"admission_{route}"
                    quarantine.append(rec)
                    continue
            rec.replay_state = "admit"
            admit.append(rec)
        return admit, quarantine, skip

    def apply(
        self,
        *,
        batch_id: str,
        rows: Iterable[RecoveryRecord],
        write_target: Callable[[str, str, dict[str, Any]], Any],
        hash_exists_func: Callable[[str], bool] | None = None,
        allow_source_prefix: str = "shadow:",
    ) -> dict[str, Any]:
        total = 0
        replayed = 0
        skipped = 0
        failed = 0
        for rec in rows:
            total += 1
            if rec.content_hash and hash_exists_func:
                try:
                    if bool(hash_exists_func(rec.content_hash)):
                        skipped += 1
                        rec.replay_state = "skip"
                        rec.failure_reason = "already_exists"
                        continue
                except RuntimeError as exc:
                    logger.warning("hash_exists_func failed for content hash check: %s", exc)
            src = (
                rec.source if str(rec.source).startswith(allow_source_prefix) else f"{allow_source_prefix}{rec.source}"
            )
            try:
                write_target(
                    rec.text,
                    src,
                    {
                        "trust_level": "shadow_recovery",
                        "recovery_batch_id": batch_id,
                        "recovery_origin": rec.origin,
                        "recovery_event_id": rec.event_id,
                    },
                )
                replayed += 1
                rec.replay_state = "replayed"
                rec.failure_reason = ""
            except (RuntimeError, OSError, ValueError) as exc:
                failed += 1
                rec.replay_state = "failed"
                rec.failure_reason = str(exc)
        self._append_batch_journal(
            {
                "batch_id": batch_id,
                "ts": utc_now_iso(),
                "total": total,
                "replayed": replayed,
                "skipped": skipped,
                "failed": failed,
                "status": "success" if failed == 0 else "partial",
            }
        )
        return {
            "batch_id": batch_id,
            "total": total,
            "replayed": replayed,
            "skipped": skipped,
            "failed": failed,
            "status": "success" if failed == 0 else "partial",
        }

    def quarantine(self, rows: Iterable[RecoveryRecord], reason: str = "recovery_guard") -> dict[str, Any]:
        cnt = 0
        last = ""
        sev = "low"
        for rec in rows:
            out = self.quarantine_store.append(rec.to_dict(), reason=reason)
            cnt += 1
            last = str(out.get("file", ""))
            sev = str(out.get("severity", sev))
        return {"status": "success", "quarantined": cnt, "file": last, "severity": sev}

    def _append_batch_journal(self, payload: dict[str, Any]) -> None:
        with self.batch_journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(payload or {}), ensure_ascii=False) + "\n")
