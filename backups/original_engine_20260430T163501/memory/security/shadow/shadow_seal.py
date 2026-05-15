from __future__ import annotations

import inspect
import uuid
from pathlib import Path
from typing import Any, Dict

from .shadow_manifest_guard import ShadowManifestGuard
from .shadow_schema import SealManifest, utc_now_iso


class ShadowSeal:
    def __init__(self, shadow_dir: Path, *, backup_dir: Path | None = None, immutable_enabled: bool = False) -> None:
        self.shadow_dir = shadow_dir
        self.shadow_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_file = self.shadow_dir / "seal_manifest.json"
        self.manifest_guard = ShadowManifestGuard(
            self.shadow_dir,
            backup_dir=backup_dir,
            immutable_enabled=immutable_enabled,
        )
        self.manifest_signature_valid = True
        self._manifest = self._load()
        self._allowed_callers = (
            "shadow_guard.py",
            "shadow_recovery.py",
            "shadow_cli.py",
            "core.py",
        )
        self._history_max_keep = 200
        if self._compact_history(max_keep=self._history_max_keep):
            self._save()

    def _authorized(self) -> bool:
        try:
            for fr in inspect.stack()[2:]:
                fname = str(getattr(fr, "filename", "") or "")
                if any(tok in fname for tok in self._allowed_callers):
                    return True
        except Exception:
            return False
        return False

    def _load(self) -> SealManifest:
        if not self.manifest_file.exists():
            return SealManifest()
        try:
            obj, sig_ok, reason = self.manifest_guard.read_manifest(self.manifest_file)
            self.manifest_signature_valid = bool(sig_ok)
            if not sig_ok:
                # Security-first tilt: keep system sealed on manifest signature issues.
                return SealManifest(
                    sealed=True,
                    seal_level="hard",
                    mode="sealed",
                    sealed_at=utc_now_iso(),
                    reason=str(reason or "manifest_signature_invalid"),
                    history=[{"ts": utc_now_iso(), "event": "manifest_signature_invalid"}],
                )
            return SealManifest(
                sealed=bool(obj.get("sealed", False)),
                seal_level=str(obj.get("seal_level", "hard") or "hard"),
                mode=str(obj.get("mode", "active")),
                sealed_at=str(obj.get("sealed_at", "")),
                reason=str(obj.get("reason", "")),
                seal_session_id=str(obj.get("seal_session_id", "")),
                sealed_write_count=int(obj.get("sealed_write_count", 0) or 0),
                write_error_streak=int(obj.get("write_error_streak", 0) or 0),
                last_recovered_at=str(obj.get("last_recovered_at", "")),
                minimal_survival_reason=str(obj.get("minimal_survival_reason", "")),
                history=list(obj.get("history", [])),
            )
        except Exception:
            self.manifest_signature_valid = False
            return SealManifest()

    def _save(self) -> None:
        self._compact_history(max_keep=self._history_max_keep)
        self.manifest_file.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_guard.write_manifest(self.manifest_file, self._manifest.to_dict())
        self.manifest_signature_valid = True

    def _compact_history(self, max_keep: int = 200) -> bool:
        history = list(getattr(self._manifest, "history", []) or [])
        if not history:
            return False
        original = list(history)
        compressed: list[dict] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            evt = str(item.get("event", ""))
            if (
                evt == "seal_update"
                and compressed
                and str(compressed[-1].get("event", "")) == "seal_update"
                and str(compressed[-1].get("reason", "")) == str(item.get("reason", ""))
                and str(compressed[-1].get("seal_level", "")) == str(item.get("seal_level", ""))
                and str(compressed[-1].get("seal_session_id", "")) == str(item.get("seal_session_id", ""))
            ):
                prev = dict(compressed[-1])
                prev["_repeat"] = int(prev.get("_repeat", 1) or 1) + 1
                prev["ts_last"] = str(item.get("ts", prev.get("ts", "")))
                compressed[-1] = prev
                continue
            compressed.append(dict(item))

        if len(compressed) <= int(max_keep):
            changed = compressed != original
            self._manifest.history = compressed
            return changed

        dropped = len(compressed) - int(max_keep)
        tail = compressed[-int(max_keep) :]
        summary = {
            "ts": utc_now_iso(),
            "event": "history_compacted",
            "dropped": dropped,
            "remaining": len(tail),
        }
        self._manifest.history = [summary] + tail
        return self._manifest.history != original

    def status(self) -> Dict[str, Any]:
        out = self._manifest.to_dict()
        out["manifest_signature_valid"] = bool(self.manifest_signature_valid)
        return out

    def mode(self) -> str:
        if str(self._manifest.mode) == "minimal_survival":
            return "minimal_survival"
        return "sealed" if self._manifest.sealed else "active"

    def is_sealed(self) -> bool:
        return bool(self._manifest.sealed)

    def seal_level(self) -> str:
        level = str(getattr(self._manifest, "seal_level", "hard") or "hard").lower()
        return level if level in {"soft", "hard"} else "hard"

    def mark_recovering(self) -> None:
        if not self._authorized():
            return
        self._manifest.mode = "recovering"
        self._save()

    def enter_minimal_survival(self, reason: str) -> Dict[str, Any]:
        if not self._authorized():
            out = self.status()
            out["rejected"] = True
            out["reason"] = "enter_minimal_survival_call_not_authorized"
            return out
        self._manifest.mode = "minimal_survival"
        self._manifest.sealed = True
        self._manifest.minimal_survival_reason = str(reason or "capacity_guard")
        self._manifest.history.append(
            {
                "ts": utc_now_iso(),
                "event": "enter_minimal_survival",
                "reason": self._manifest.minimal_survival_reason,
            }
        )
        self._save()
        return self.status()

    def exit_minimal_survival(self, reason: str = "manual") -> Dict[str, Any]:
        if not self._authorized():
            out = self.status()
            out["rejected"] = True
            out["reason"] = "exit_minimal_survival_call_not_authorized"
            return out
        if self._manifest.mode != "minimal_survival":
            return self.status()
        self._manifest.mode = "sealed" if self._manifest.sealed else "active"
        self._manifest.history.append(
            {
                "ts": utc_now_iso(),
                "event": "exit_minimal_survival",
                "reason": str(reason or "manual"),
            }
        )
        self._manifest.minimal_survival_reason = ""
        self._save()
        return self.status()

    def trigger_seal(self, reason: str, level: str = "hard") -> Dict[str, Any]:
        if not self._authorized():
            out = self.status()
            out["rejected"] = True
            out["reason"] = "seal_call_not_authorized"
            return out
        level_norm = str(level or "hard").lower()
        if level_norm not in {"soft", "hard"}:
            level_norm = "hard"
        if not self._manifest.sealed:
            self._manifest.sealed = True
            self._manifest.mode = "sealed"
            self._manifest.sealed_at = utc_now_iso()
            self._manifest.reason = str(reason or "")
            self._manifest.seal_session_id = f"seal-{uuid.uuid4().hex[:12]}"
            self._manifest.seal_level = level_norm
            self._manifest.write_error_streak = 0
            self._manifest.history.append(
                {
                    "ts": self._manifest.sealed_at,
                    "event": "seal",
                    "reason": self._manifest.reason,
                    "seal_session_id": self._manifest.seal_session_id,
                    "seal_level": self._manifest.seal_level,
                }
            )
            self._save()
        else:
            # allow operator to promote soft -> hard without unsealing
            prev = self.seal_level()
            self._manifest.seal_level = "hard" if (prev == "hard" or level_norm == "hard") else "soft"
            self._manifest.reason = str(reason or self._manifest.reason)
            self._manifest.history.append(
                {
                    "ts": utc_now_iso(),
                    "event": "seal_update",
                    "reason": self._manifest.reason,
                    "seal_session_id": self._manifest.seal_session_id,
                    "seal_level": self._manifest.seal_level,
                }
            )
            self._save()
        return self.status()

    def inc_sealed_writes(self, n: int = 1) -> None:
        if not self._authorized():
            return
        self._manifest.sealed_write_count += max(0, int(n))
        self._save()

    def note_write_error(self, threshold: int = 3, reason: str = "write_error") -> Dict[str, Any]:
        if not self._authorized():
            out = self.status()
            out["rejected"] = True
            out["reason"] = "note_write_error_call_not_authorized"
            return out
        th = max(1, int(threshold))
        self._manifest.write_error_streak = int(self._manifest.write_error_streak) + 1
        promoted = False
        if self.seal_level() == "soft" and int(self._manifest.write_error_streak) >= th:
            self._manifest.seal_level = "hard"
            promoted = True
            self._manifest.history.append(
                {
                    "ts": utc_now_iso(),
                    "event": "seal_promote",
                    "from": "soft",
                    "to": "hard",
                    "reason": str(reason or "write_error"),
                    "threshold": th,
                }
            )
        self._save()
        out = self.status()
        out["promoted_to_hard"] = promoted
        out["threshold"] = th
        return out

    def note_write_success(self) -> None:
        if not self._authorized():
            return
        if int(self._manifest.write_error_streak) <= 0:
            return
        self._manifest.write_error_streak = 0
        self._save()

    def clear_seal(self, reason: str = "recovered") -> Dict[str, Any]:
        if not self._authorized():
            out = self.status()
            out["rejected"] = True
            out["reason"] = "clear_seal_call_not_authorized"
            return out
        self._manifest.history.append({"ts": utc_now_iso(), "event": "unseal", "reason": reason})
        self._manifest.sealed = False
        self._manifest.seal_level = "hard"
        self._manifest.mode = "active"
        self._manifest.write_error_streak = 0
        self._manifest.last_recovered_at = utc_now_iso()
        self._manifest.reason = ""
        self._manifest.seal_session_id = ""
        self._manifest.minimal_survival_reason = ""
        self._save()
        return self.status()

    def list_manifest_snapshots(self, limit: int = 20) -> list[dict]:
        return self.manifest_guard.list_manifest_snapshots(limit=limit)

    def restore_manifest_snapshot(self, snapshot_path: str) -> Dict[str, Any]:
        out = self.manifest_guard.restore_manifest_snapshot(self.manifest_file, snapshot_path)
        if str(out.get("status")) == "success":
            self._manifest = self._load()
        return out
