from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import shutil
import stat
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .shadow_schema import ShadowEvent, utc_now_iso

logger = logging.getLogger(__name__)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class ShadowLedger:
    """
    Append-only event ledger with lightweight checkpoint hashes.

    Performance enhancement:
    - no per-event prev_hash lookup
    - compute checkpoint hash every N events (default 100)
    """

    def __init__(
        self,
        shadow_dir: Path,
        *,
        backup_dir: Path | None = None,
        payload_threshold: int = 500,
        checkpoint_interval: int = 100,
        snapshot_interval: int = 100,
        snapshot_keep: int = 3,
        spool_encryptor: Callable[[str], str] | None = None,
        spool_decryptor: Callable[[str], str] | None = None,
        spool_encryption_enabled: bool = False,
        immutable_enabled: bool = False,
    ) -> None:
        self.shadow_dir = shadow_dir
        self.shadow_dir.mkdir(parents=True, exist_ok=True)
        self.payload_dir = self.shadow_dir / "payloads"
        self.payload_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir = self.shadow_dir / "archive"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.corrupt_dir = self.shadow_dir / "corrupt"
        self.corrupt_dir.mkdir(parents=True, exist_ok=True)
        self.events_file = self.shadow_dir / "shadow_events.jsonl"
        self.backup_dir = backup_dir
        self.backup_events_file = (Path(backup_dir) / "shadow_events.jsonl") if backup_dir is not None else None
        self._backup_write_disabled = False
        self.spool_file = self.shadow_dir / "shadow_spool.jsonl"
        self.checkpoints_file = self.shadow_dir / "shadow_checkpoints.jsonl"
        self.verify_file = self.shadow_dir / "shadow_verify.jsonl"
        self.payload_threshold = max(100, int(payload_threshold))
        self.checkpoint_interval = max(1, int(checkpoint_interval))
        self.snapshot_interval = max(1, int(snapshot_interval))
        self.snapshot_keep = max(1, int(snapshot_keep))
        self.spool_encryptor = spool_encryptor
        self.spool_decryptor = spool_decryptor
        self.spool_encryption_enabled = bool(spool_encryption_enabled)
        self.immutable_enabled = bool(immutable_enabled)
        self._seq = self._read_last_seq()
        self._checkpoint_buffer: list[str] = []
        self.max_content_chars = 100 * 1024

    def _sanitize_field(self, value: Any, *, max_len: int = 256) -> str:
        s = str(value or "")
        s = s.replace("\x00", " ").replace("\r", " ").replace("\n", " ").strip()
        if len(s) > max_len:
            s = s[:max_len]
        return s

    def _sanitize_content(self, content: str) -> tuple[str, bool]:
        text = str(content or "")
        text = text.replace("\x00", "")
        truncated = False
        if len(text) > self.max_content_chars:
            text = text[: self.max_content_chars]
            truncated = True
        return text, truncated

    def _read_last_seq(self) -> int:
        if not self.events_file.exists():
            return 0
        last_seq = 0
        try:
            with self.events_file.open("r", encoding="utf-8") as f:
                for line in f:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    last_seq = max(last_seq, int(obj.get("seq", 0) or 0))
        except OSError:
            return 0
        return last_seq

    def _append_line(self, path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._set_mutable(path)
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(path, 0o600)
            except OSError as exc:
                logger.debug("Failed chmod events file %s: %s", path, exc)
        finally:
            self._set_immutable(path)

    def _safe_append_with_fallback(self, path: Path, line: str) -> None:
        tmp_fallback = Path("/tmp/openclaw_shadow_events_fallback.jsonl")
        try:
            self._append_line(path, line)
            return
        except OSError as exc:
            logger.warning("Failed appending to primary events file %s: %s", path, exc)
        try:
            self._append_line(tmp_fallback, line)
            return
        except OSError as exc:
            logger.warning("Failed appending to fallback events file %s: %s", tmp_fallback, exc)

        # last-resort: never silently drop
        logger.error("Failed appending shadow event to both primary and fallback stores; event=%s", line)

    def _store_payload(self, event_id: str, content: str) -> str:
        payload_file = self.payload_dir / f"{event_id}.json"
        payload = {"event_id": event_id, "content": content, "ts": utc_now_iso()}
        payload_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload_file.name

    def _summary(self, content: str, max_chars: int = 200) -> str:
        s = str(content or "").replace("\n", " ").strip()
        if len(s) <= max_chars:
            return s
        return s[:max_chars] + "..."

    def _checkpoint_if_needed(self) -> None:
        if self._seq <= 0:
            return
        if self._seq % self.checkpoint_interval != 0:
            return
        if not self._checkpoint_buffer:
            return
        joined = "|".join(self._checkpoint_buffer)
        cp_hash = _sha256_text(joined)
        record = {
            "ts": utc_now_iso(),
            "upto_seq": self._seq,
            "interval": self.checkpoint_interval,
            "checkpoint_hash": cp_hash,
        }
        self._safe_append_with_fallback(self.checkpoints_file, _json_line(record))
        self._checkpoint_buffer.clear()

    def _snapshot_if_needed(self) -> None:
        if self._seq <= 0:
            return
        if self._seq % self.snapshot_interval != 0:
            return
        if not self.events_file.exists():
            return
        stamp = utc_now_iso().replace(":", "").replace("-", "")
        snap = self.shadow_dir / f"shadow_events.{stamp}.bak"
        try:
            shutil.copy2(self.events_file, snap)
        except OSError:
            return
        snaps = sorted(self.shadow_dir.glob("shadow_events.*.bak"), reverse=True)
        for old in snaps[self.snapshot_keep :]:
            try:
                old.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Failed pruning old snapshot %s: %s", old, exc)

    def list_snapshots(self, limit: int = 10) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in sorted(self.shadow_dir.glob("shadow_events.*.bak"), reverse=True)[: max(1, int(limit))]:
            try:
                out.append({"path": str(p), "size": p.stat().st_size, "mtime": p.stat().st_mtime})
            except OSError:
                continue
        return out

    def verify_snapshot(self, snapshot_path: str) -> dict[str, Any]:
        p = Path(snapshot_path)
        if not p.exists():
            return {"ok": False, "reason": "snapshot_missing", "path": str(p)}
        try:
            total = 0
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    raw = line.strip()
                    if not raw:
                        continue
                    json.loads(raw)
                    total += 1
            return {"ok": True, "path": str(p), "rows": total}
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "reason": f"snapshot_invalid:{exc}", "path": str(p)}

    def append_event(
        self,
        *,
        event_type: str,
        action: str,
        source: str,
        mode: str,
        ok: bool,
        content: str = "",
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._seq += 1
        event_id = f"{self._seq:010d}-{uuid.uuid4().hex[:8]}"
        text, truncated = self._sanitize_content(str(content or ""))
        payload_file = ""
        if len(text) > self.payload_threshold:
            try:
                payload_file = self._store_payload(event_id, text)
            except OSError:
                payload_file = ""

        content_hash = _sha256_text(text) if text else ""
        event = ShadowEvent(
            event_id=event_id,
            seq=self._seq,
            ts=utc_now_iso(),
            event_type=self._sanitize_field(event_type, max_len=64),
            action=self._sanitize_field(action, max_len=64),
            source=self._sanitize_field(source, max_len=128),
            mode=mode,
            ok=bool(ok),
            error=self._sanitize_field(error, max_len=256),
            content_hash=content_hash,
            summary=self._summary(text),
            payload_file=payload_file,
            metadata=dict(metadata or {}),
        )
        if truncated:
            event.metadata["content_truncated"] = True
            event.metadata["content_max_chars"] = self.max_content_chars
        row = event.to_dict()
        line = _json_line(row)
        self._safe_append_with_fallback(self.events_file, line)
        if self.backup_events_file is not None and not self._backup_write_disabled:
            try:
                self._append_line(self.backup_events_file, line)
            except OSError as exc:
                self._backup_write_disabled = True
                logger.warning(
                    "Backup events disabled (path not writable): %s (%s)",
                    self.backup_events_file,
                    exc,
                )
        self._checkpoint_buffer.append(_sha256_text(line))
        self._checkpoint_if_needed()
        self._snapshot_if_needed()
        return row

    def append_spool(self, source: str, content: str) -> dict[str, Any]:
        spool_id = f"sp-{uuid.uuid4().hex[:10]}"
        day_bucket = utc_now_iso()[:10]
        content_raw, truncated = self._sanitize_content(str(content or ""))
        encrypted = False
        encrypted_content = content_raw
        if self.spool_encryption_enabled and self.spool_encryptor:
            try:
                encrypted_content = str(self.spool_encryptor(content_raw))
                encrypted = True
            except OSError:
                encrypted_content = content_raw
                encrypted = False
        item = {
            "spool_id": spool_id,
            "ts": utc_now_iso(),
            "source": self._sanitize_field(source, max_len=128),
            "content_hash": _sha256_text(content_raw),
            "content": encrypted_content,
            "content_encrypted": encrypted,
            "day_bucket": day_bucket,
            "replayed": False,
            "replay_attempts": 0,
            "last_error": "",
            "replayed_at": None,
            "replay_batch_id": "",
            "content_truncated": truncated,
        }
        self._safe_append_with_fallback(self.spool_file, _json_line(item))
        return item

    def read_events(self) -> Iterable[dict[str, Any]]:
        if not self.events_file.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.events_file.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rows.append(json.loads(raw))
                except (TypeError, ValueError, json.JSONDecodeError):
                    # tolerate bad line
                    continue
        return rows

    def read_spool(self) -> list[dict[str, Any]]:
        if not self.spool_file.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.spool_file.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                    if isinstance(row, dict):
                        content = str(row.get("content", ""))
                        if bool(row.get("content_encrypted", False)) and self.spool_decryptor:
                            try:
                                row["content"] = str(self.spool_decryptor(content))
                            except (TypeError, ValueError):
                                row["content_decrypt_error"] = True
                        rows.append(row)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        return rows

    def rewrite_spool(self, rows: list[dict[str, Any]]) -> None:
        self.spool_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.spool_file.with_suffix(".jsonl.tmp")
        self._set_mutable(self.spool_file)
        with tmp.open("w", encoding="utf-8") as f:
            for row in rows:
                out_row = dict(row)
                if bool(out_row.get("content_encrypted", False)) and self.spool_encryptor:
                    try:
                        plain = str(out_row.get("content", ""))
                        out_row["content"] = str(self.spool_encryptor(plain))
                    except OSError as exc:
                        logger.warning("Spool encryption failed during rewrite: %s", exc)
                f.write(_json_line(out_row) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.spool_file)
        self._set_immutable(self.spool_file)

    def _set_mutable(self, path: Path) -> None:
        if not self.immutable_enabled:
            return
        try:
            if hasattr(os, "chflags"):
                os.chflags(path, 0)
        except OSError:
            return

    def _set_immutable(self, path: Path) -> None:
        if not self.immutable_enabled:
            return
        try:
            if hasattr(os, "chflags"):
                os.chflags(path, stat.UF_IMMUTABLE)
        except OSError:
            return

    def append_verify_result(self, result: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "ts": utc_now_iso(),
            "ok": bool(result.get("ok", False)),
            "status": str(result.get("status", "")),
            "total": int(result.get("total", 0) or 0),
            "mismatch_count": len(result.get("mismatches", []) or []),
        }
        self._safe_append_with_fallback(self.verify_file, _json_line(payload))
        return payload

    def _is_recent(self, ts_text: str, days: int) -> bool:
        if days <= 0:
            return False
        try:
            raw = str(ts_text or "")
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            return dt >= (datetime.now(timezone.utc) - timedelta(days=days))
        except (TypeError, ValueError):
            return False

    def archive_replayed_spool(
        self,
        *,
        hot_days: int = 7,
        warm_days: int = 30,
        cold_days: int = 180,
    ) -> dict[str, Any]:
        rows = self.read_spool()
        if not rows:
            return {"status": "skipped", "reason": "empty_spool", "archived": 0, "kept": 0}

        kept: list[dict[str, Any]] = []
        archived = 0
        by_tier: dict[str, int] = {"warm": 0, "cold": 0}
        for row in rows:
            replayed = bool(row.get("replayed", False))
            replayed_at = str(row.get("replayed_at", "") or row.get("ts", ""))
            if not replayed or self._is_recent(replayed_at, hot_days):
                kept.append(row)
                continue

            tier = "warm"
            if not self._is_recent(replayed_at, warm_days):
                tier = "cold"
            if cold_days > 0 and not self._is_recent(replayed_at, cold_days):
                tier = "cold"
            bucket = str(replayed_at[:7] or "unknown")
            path = self.archive_dir / tier
            path.mkdir(parents=True, exist_ok=True)
            target = path / f"shadow_spool_{bucket}.jsonl"
            self._safe_append_with_fallback(target, _json_line(row))
            archived += 1
            by_tier[tier] = by_tier.get(tier, 0) + 1

        self.rewrite_spool(kept)
        return {
            "status": "success",
            "archived": archived,
            "kept": len(kept),
            "by_tier": by_tier,
            "hot_days": hot_days,
            "warm_days": warm_days,
            "cold_days": cold_days,
        }

    def _repair_jsonl_file(self, path: Path, name: str) -> dict[str, Any]:
        if not path.exists():
            return {"status": "skipped", "file": str(path), "reason": "missing"}
        good: list[str] = []
        bad: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for idx, line in enumerate(f, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    json.loads(raw)
                    good.append(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    bad.append({"line": idx, "raw": raw[:500]})
        if not bad:
            return {"status": "ok", "file": str(path), "valid_lines": len(good), "corrupt_lines": 0}

        stamp = utc_now_iso().replace(":", "").replace("-", "")
        corrupt_file = self.corrupt_dir / f"{name}.corrupt.{stamp}.jsonl"
        with corrupt_file.open("w", encoding="utf-8") as f:
            for row in bad:
                f.write(_json_line(row) + "\n")
        tmp = path.with_suffix(path.suffix + ".repair.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for raw in good:
                f.write(raw + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return {
            "status": "repaired",
            "file": str(path),
            "valid_lines": len(good),
            "corrupt_lines": len(bad),
            "corrupt_dump": str(corrupt_file),
        }

    def startup_self_heal(self) -> dict[str, Any]:
        reports = []
        reports.append(self._repair_jsonl_file(self.events_file, "events"))
        reports.append(self._repair_jsonl_file(self.spool_file, "spool"))
        reports.append(self._repair_jsonl_file(self.checkpoints_file, "checkpoints"))
        reports.append(self._repair_jsonl_file(self.verify_file, "verify"))
        repaired = sum(1 for r in reports if str(r.get("status")) == "repaired")
        corrupt = sum(int(r.get("corrupt_lines", 0) or 0) for r in reports)
        return {
            "status": "success",
            "repaired_files": repaired,
            "corrupt_lines_total": corrupt,
            "reports": reports,
        }

    def _month_key(self, ts_text: str) -> str:
        raw = str(ts_text or "").strip()
        if not raw:
            return ""
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m")
        except (TypeError, ValueError):
            return ""

    def rotate_events_monthly(self) -> dict[str, Any]:
        if not self.events_file.exists():
            return {"status": "skipped", "reason": "events_missing", "archived": 0, "kept": 0}
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        archived = 0
        kept = 0
        by_month: dict[str, int] = {}
        keep_lines: list[str] = []

        with self.events_file.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    # keep unparseable rows for startup_self_heal path
                    keep_lines.append(raw)
                    kept += 1
                    continue
                mk = self._month_key(str(obj.get("ts", "")))
                if not mk or mk == current_month:
                    keep_lines.append(raw)
                    kept += 1
                    continue
                target = self.archive_dir / "events"
                target.mkdir(parents=True, exist_ok=True)
                gz = target / f"shadow_events.{mk}.jsonl.gz"
                with gzip.open(gz, "at", encoding="utf-8") as out:
                    out.write(raw + "\n")
                archived += 1
                by_month[mk] = by_month.get(mk, 0) + 1

        tmp = self.events_file.with_suffix(".rotate.tmp")
        self._set_mutable(self.events_file)
        try:
            with tmp.open("w", encoding="utf-8") as f:
                for raw in keep_lines:
                    f.write(raw + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.events_file)
        finally:
            self._set_immutable(self.events_file)
        self._seq = self._read_last_seq()
        return {
            "status": "success",
            "archived": archived,
            "kept": kept,
            "current_month": current_month,
            "by_month": by_month,
        }

    def verify_checkpoints(self) -> dict[str, Any]:
        """
        Verify checkpoint hashes against event lines.
        Returns mismatches for observability and manual repair.
        """
        if not self.checkpoints_file.exists():
            return {"ok": True, "status": "no_checkpoints", "total": 0, "mismatches": []}
        if not self.events_file.exists():
            return {
                "ok": False,
                "status": "events_missing",
                "total": 0,
                "mismatches": ["events_missing"],
            }

        checkpoints: list[dict[str, Any]] = []
        with self.checkpoints_file.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    checkpoints.append(json.loads(raw))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        if not checkpoints:
            return {"ok": True, "status": "no_valid_checkpoints", "total": 0, "mismatches": []}

        events: list[dict[str, Any]] = []
        with self.events_file.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue

        seq_to_linehash: dict[int, str] = {}
        for event in events:
            try:
                seq = int(event.get("seq", 0) or 0)
            except (TypeError, ValueError):
                continue
            if seq <= 0:
                continue
            line = _json_line(event)
            seq_to_linehash[seq] = _sha256_text(line)

        mismatches: list[dict[str, Any]] = []
        for cp in checkpoints:
            try:
                upto = int(cp.get("upto_seq", 0) or 0)
                interval = int(cp.get("interval", self.checkpoint_interval) or self.checkpoint_interval)
                expected = str(cp.get("checkpoint_hash", ""))
            except (TypeError, ValueError):
                continue
            if upto <= 0 or interval <= 0 or not expected:
                continue
            start = max(1, upto - interval + 1)
            parts: list[str] = []
            missing = False
            for seq in range(start, upto + 1):
                hv = seq_to_linehash.get(seq)
                if not hv:
                    missing = True
                    break
                parts.append(hv)
            if missing:
                mismatches.append({"upto_seq": upto, "reason": "events_missing_for_range"})
                continue
            actual = _sha256_text("|".join(parts))
            if actual != expected:
                mismatches.append(
                    {
                        "upto_seq": upto,
                        "reason": "hash_mismatch",
                        "expected": expected,
                        "actual": actual,
                    }
                )

        return {
            "ok": len(mismatches) == 0,
            "status": "ok" if len(mismatches) == 0 else "mismatch",
            "total": len(checkpoints),
            "mismatches": mismatches,
        }

    def rebuild_checkpoints_from_events(self, interval: int | None = None) -> dict[str, Any]:
        iv = max(1, int(interval or self.checkpoint_interval))
        events = list(self.read_events())
        seq_hash: dict[int, str] = {}
        max_seq = 0
        for event in events:
            try:
                seq = int(event.get("seq", 0) or 0)
            except (TypeError, ValueError):
                continue
            if seq <= 0:
                continue
            max_seq = max(max_seq, seq)
            seq_hash[seq] = _sha256_text(_json_line(event))

        records: list[dict[str, Any]] = []
        upto = iv
        while upto <= max_seq:
            start = max(1, upto - iv + 1)
            parts: list[str] = []
            complete = True
            for seq in range(start, upto + 1):
                hv = seq_hash.get(seq)
                if not hv:
                    complete = False
                    break
                parts.append(hv)
            if complete and parts:
                records.append(
                    {
                        "ts": utc_now_iso(),
                        "upto_seq": upto,
                        "interval": iv,
                        "checkpoint_hash": _sha256_text("|".join(parts)),
                    }
                )
            upto += iv

        tmp = self.checkpoints_file.with_suffix(".rebuild.tmp")
        self._set_mutable(self.checkpoints_file)
        try:
            with tmp.open("w", encoding="utf-8") as f:
                for rec in records:
                    f.write(_json_line(rec) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.checkpoints_file)
            try:
                os.chmod(self.checkpoints_file, 0o600)
            except OSError as exc:
                logger.debug("Failed chmod checkpoints file %s: %s", self.checkpoints_file, exc)
        finally:
            self._set_immutable(self.checkpoints_file)
        return {
            "status": "success",
            "interval": iv,
            "events": len(events),
            "max_seq": max_seq,
            "checkpoints_written": len(records),
        }
