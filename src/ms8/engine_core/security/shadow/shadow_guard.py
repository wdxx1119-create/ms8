from __future__ import annotations

import base64
import hashlib
import inspect
import json
import os
import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from .shadow_audit import ShadowAudit
from .shadow_capacity_guard import ShadowCapacityGuard
from .shadow_checkpoint_guard import ShadowCheckpointGuard
from .shadow_control_gate import GateRequest, ShadowControlGate
from .shadow_ledger import ShadowLedger
from .shadow_locking import ShadowLocking
from .shadow_permissions import ensure_shadow_permissions
from .shadow_platform_log import emit_system_log
from .shadow_recovery import ShadowRecovery
from .shadow_recovery_guard import ShadowRecoveryGuard
from .shadow_seal import ShadowSeal
from .shadow_tokens import ShadowTokenManager


class _NoCrypto:
    def is_enabled(self) -> bool:
        return False

    def is_unlocked(self) -> bool:
        return False

    def encrypt_before_write(self, data: bytes, file_type: str = "") -> bytes:
        return data

    def decrypt_after_read(self, blob: bytes, allow_plaintext: bool = True) -> bytes:
        return blob


class _ShadowSealView:
    """Read-only view to avoid exposing mutating seal internals."""

    def __init__(self, seal: ShadowSeal) -> None:
        self._seal = seal

    def status(self) -> dict[str, Any]:
        return self._seal.status()

    def mode(self) -> str:
        return self._seal.mode()

    def is_sealed(self) -> bool:
        return self._seal.is_sealed()

    def seal_level(self) -> str:
        return self._seal.seal_level()


class ShadowSystem:
    """
    Minimal survival layer:
    - always-on audit ledger
    - sealed-mode write takeover
    - replay/recovery
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        sec_cfg = config.get("settings", {}).get("memory", {}).get("security", {}).get("shadow", {})
        memory_dir = Path(config["memory_dir"])
        root = sec_cfg.get("shadow_dir", memory_dir / "security" / "shadow_data")
        self.shadow_dir = Path(root).expanduser()
        self.enabled = bool(sec_cfg.get("enabled", True))
        self.soft_to_hard_error_threshold = int(sec_cfg.get("soft_to_hard_error_threshold", 3))
        self.auto_seal_on_write_error_level = str(sec_cfg.get("auto_seal_on_write_error_level", "soft") or "soft")
        self.auto_self_heal_on_startup = bool(sec_cfg.get("auto_self_heal_on_startup", True))
        self.spool_encryption_enabled = bool(sec_cfg.get("spool_encryption_enabled", True))
        self.spool_archive_hot_days = int(sec_cfg.get("spool_archive_hot_days", 7))
        self.spool_archive_warm_days = int(sec_cfg.get("spool_archive_warm_days", 30))
        self.spool_archive_cold_days = int(sec_cfg.get("spool_archive_cold_days", 180))
        self.minimal_survival_enabled = bool(sec_cfg.get("minimal_survival_enabled", True))
        self.minimal_survival_shadow_max_mb = float(sec_cfg.get("minimal_survival_shadow_max_mb", 512.0))
        self.minimal_survival_payload_max_mb = float(sec_cfg.get("minimal_survival_payload_max_mb", 256.0))
        self.minimal_survival_enter_pct = float(sec_cfg.get("minimal_survival_enter_pct", 0.95))
        self.minimal_survival_exit_pct = float(sec_cfg.get("minimal_survival_exit_pct", 0.80))
        self.minimal_survival_read_sample_every = int(sec_cfg.get("minimal_survival_read_sample_every", 10) or 10)
        default_backup_dir = str(self.shadow_dir.parent / "shadow_backup")
        self.backup_dir = Path(sec_cfg.get("backup_dir", default_backup_dir)).expanduser()
        shadow_dir_str = str(self.shadow_dir)
        default_immutable = not (shadow_dir_str.startswith("/tmp") or shadow_dir_str.startswith("/var/folders/"))
        self.immutable_enabled = bool(sec_cfg.get("immutable_enabled", default_immutable))
        self.stack_guard_enabled = bool(sec_cfg.get("stack_guard_enabled", default_immutable))
        default_prefixes = [str(Path(__file__).resolve().parents[2])]
        self.stack_guard_allowed_prefixes = [
            str(Path(p).expanduser())
            for p in (sec_cfg.get("stack_guard_allowed_prefixes", default_prefixes) or default_prefixes)
        ]
        self.startup_integrity_emit_cooldown_seconds = int(
            sec_cfg.get("startup_integrity_emit_cooldown_seconds", 600) or 600
        )
        self._startup_integrity_emit_state_file = self.shadow_dir / "startup_integrity_emit_state.json"
        self._crypto: Any
        try:
            from ..encryption.crypto_manager import get_crypto_manager

            if "workspace_dir" in config and "memory_dir" in config:
                self._crypto = get_crypto_manager(config)
            else:
                self._crypto = _NoCrypto()
        except (ImportError, OSError, TypeError, ValueError):
            self._crypto = _NoCrypto()
        self.ledger = ShadowLedger(
            self.shadow_dir,
            backup_dir=self.backup_dir,
            payload_threshold=int(sec_cfg.get("payload_threshold_chars", 500)),
            checkpoint_interval=int(sec_cfg.get("checkpoint_interval", 100)),
            snapshot_interval=int(sec_cfg.get("snapshot_interval", 100)),
            snapshot_keep=int(sec_cfg.get("snapshot_keep", 3)),
            spool_encryptor=self._encrypt_spool_payload,
            spool_decryptor=self._decrypt_spool_payload,
            spool_encryption_enabled=self.spool_encryption_enabled,
            immutable_enabled=self.immutable_enabled,
        )
        self._seal = ShadowSeal(self.shadow_dir, backup_dir=self.backup_dir, immutable_enabled=self.immutable_enabled)
        # Backward-compatible public read-only view.
        self.seal = _ShadowSealView(self._seal)
        self.audit = ShadowAudit(self.shadow_dir, immutable_enabled=self.immutable_enabled)
        self.locking = ShadowLocking()
        self.tokens = ShadowTokenManager()
        self.gate = ShadowControlGate(self.locking, self.tokens, self.audit)
        self.capacity_guard = ShadowCapacityGuard(
            self.shadow_dir,
            shadow_max_mb=self.minimal_survival_shadow_max_mb,
            payload_max_mb=self.minimal_survival_payload_max_mb,
            enter_pct=self.minimal_survival_enter_pct,
            exit_pct=self.minimal_survival_exit_pct,
        )
        self._minimal_read_counter = 0

        def _admission_check(text: str, metadata: dict[str, Any]) -> dict[str, Any]:
            try:
                from ...admission_compat import evaluate_candidate
                decision = evaluate_candidate(text=str(text or ""), metadata=dict(metadata or {}))
                if hasattr(decision, "to_dict"):
                    return cast(dict[str, Any], decision.to_dict())
                return {"route": str(getattr(decision, "route", "accepted"))}
            except (ImportError, TypeError, ValueError, AttributeError):
                return {"route": "accepted", "reasons": ["admission_unavailable_fallback"]}

        self.recovery_guard = ShadowRecoveryGuard(self.ledger, self.shadow_dir, admission_check=_admission_check)
        self.recovery = ShadowRecovery(self.ledger, self._seal, self.recovery_guard)
        self.checkpoint_guard = ShadowCheckpointGuard(self.ledger)
        self._default_tokens = {
            "memory_core": self.tokens.issue_token(
                "memory_core",
                permissions={
                    "seal:trigger",
                    "seal:clear",
                    "shadow:replay",
                    "shadow:recover",
                    "shadow:verify",
                    "shadow:restore_snapshot",
                    "shadow:manifest_restore",
                    "shadow:backup_sync",
                    "shadow:restore_backup_snapshot",
                },
                ttl_seconds=86400,
            ),
            "trusted_cli": self.tokens.issue_token(
                "trusted_cli",
                permissions={
                    "seal:trigger",
                    "seal:clear",
                    "shadow:replay",
                    "shadow:recover",
                    "shadow:verify",
                    "shadow:restore_snapshot",
                    "shadow:manifest_restore",
                    "shadow:backup_sync",
                    "shadow:restore_backup_snapshot",
                },
                ttl_seconds=86400,
            ),
            "system_bootstrap": self.tokens.issue_token(
                "system_bootstrap",
                permissions={"seal:trigger", "shadow:verify"},
                ttl_seconds=86400,
            ),
        }
        # Target registry: no external write_func injection.
        self._bound_targets: dict[str, bool] = {}
        self._startup_findings: list[str] = []
        self._startup_manifest_untrusted: bool = False
        # Permission hardening at startup.
        ensure_shadow_permissions(
            self.shadow_dir,
            backup_dir=self.backup_dir,
            audit_cb=lambda e, m: self.record_mode(
                "protect", source="shadow:permissions", ok=True, metadata={"event": e, **m}
            ),
        )
        if self.auto_self_heal_on_startup:
            try:
                self.ledger.startup_self_heal()
            except OSError as exc:
                print(f"[ShadowGuard] Startup self-heal failed: {exc}")
        try:
            self._startup_integrity_scan()
        except OSError as exc:
            print(f"[ShadowGuard] Startup integrity scan failed: {exc}")

    def _stack_guard_ok(self) -> bool:
        if not self.stack_guard_enabled:
            return True
        try:
            stack = inspect.stack()
            for fr in stack[2:]:
                p = str(getattr(fr, "filename", "") or "")
                if not p:
                    continue
                for prefix in self.stack_guard_allowed_prefixes:
                    if p.startswith(prefix):
                        return True
            return False
        except OSError:
            return False

    def _startup_integrity_scan(self) -> dict[str, Any]:
        findings: list[str] = []
        if not self.ledger.events_file.exists():
            findings.append("events_missing")
        if not self._seal.manifest_file.exists():
            findings.append("manifest_missing")
        gate = self.checkpoint_guard.verify_gate()
        if not bool(gate.get("ok", False)):
            reason = str(gate.get("reason", "checkpoint_failed"))
            rebased = False
            if reason == "checkpoint_mismatch":
                try:
                    rb = self.ledger.rebuild_checkpoints_from_events()
                    gate2 = self.checkpoint_guard.verify_gate()
                    rebased = bool(gate2.get("ok", False))
                    if rebased:
                        self.record_mode(
                            "protect",
                            source="shadow:checkpoint_guard",
                            ok=True,
                            metadata={"event": "checkpoint_rebased", "result": rb},
                        )
                        gate = gate2
                except OSError:
                    rebased = False
            if not rebased:
                findings.append(reason)
        st = self._seal.status()
        if not bool(st.get("manifest_signature_valid", True)):
            findings.append("manifest_signature_invalid")
        self._startup_findings = list(findings)
        self._startup_manifest_untrusted = "manifest_signature_invalid" in findings
        signature = self._startup_integrity_signature(findings)
        should_emit = self._should_emit_startup_integrity(signature)
        if findings:
            if should_emit:
                emit_system_log("shadow_startup_integrity_failed", {"findings": findings})
                self.record_mode(
                    "protect",
                    source="shadow:startup_integrity",
                    ok=False,
                    error="startup_integrity_failed",
                    metadata={"findings": findings, "signature": signature},
                )
            try:
                cur = self._seal.status()
                already = bool(cur.get("sealed", False)) and str(cur.get("reason", "")) == "startup_integrity_failed"
                if not already:
                    self._seal.trigger_seal(reason="startup_integrity_failed", level="hard")
            except OSError as exc:
                print(f"[ShadowGuard] Failed triggering startup integrity seal: {exc}")
        else:
            if should_emit:
                self.record_mode(
                    "protect",
                    source="shadow:startup_integrity",
                    ok=True,
                    metadata={"findings": [], "signature": signature},
                )
        if should_emit:
            self._mark_startup_integrity_emitted(signature)
        return {"ok": len(findings) == 0, "findings": findings}

    def _startup_integrity_signature(self, findings: list[str]) -> str:
        if not findings:
            return "ok"
        clean = sorted({str(x) for x in findings if str(x)})
        return "fail:" + "|".join(clean)

    def _should_emit_startup_integrity(self, signature: str) -> bool:
        cooldown = max(0, int(self.startup_integrity_emit_cooldown_seconds))
        if cooldown == 0:
            return True
        try:
            if not self._startup_integrity_emit_state_file.exists():
                return True
            obj = json.loads(self._startup_integrity_emit_state_file.read_text(encoding="utf-8"))
            prev_sig = str(obj.get("signature", ""))
            prev_ts = str(obj.get("ts", ""))
            if prev_sig != signature:
                return True
            if not prev_ts:
                return True
            prev_dt = datetime.fromisoformat(prev_ts)
            now = datetime.now(timezone.utc)
            if prev_dt.tzinfo is None:
                prev_dt = prev_dt.replace(tzinfo=timezone.utc)
            elapsed = (now - prev_dt).total_seconds()
            return elapsed >= cooldown
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return True

    def _mark_startup_integrity_emitted(self, signature: str) -> None:
        try:
            payload = {"signature": str(signature), "ts": datetime.now(timezone.utc).isoformat()}
            tmp = self._startup_integrity_emit_state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._startup_integrity_emit_state_file)
            try:
                os.chmod(self._startup_integrity_emit_state_file, 0o600)
            except OSError as exc:
                print(f"[ShadowGuard] Failed chmod startup integrity state file: {exc}")
        except OSError as exc:
            print(f"[ShadowGuard] Failed writing startup integrity state file: {exc}")

    def reset_checkpoint(self) -> dict[str, Any]:
        try:
            out = self.ledger.rebuild_checkpoints_from_events()
            verify = self.checkpoint_guard.verify_gate()
            ok = bool(verify.get("ok", False))
            self.record_mode(
                "protect",
                source="shadow:checkpoint_guard",
                ok=ok,
                metadata={"event": "checkpoint_reset_manual", "result": out, "verify": verify},
            )
            return {"status": "success" if ok else "partial", "rebuild": out, "verify": verify}
        except OSError as exc:
            return {"status": "error", "reason": f"checkpoint_reset_failed:{exc}"}

    def is_enabled(self) -> bool:
        return bool(self.enabled)

    def is_sealed(self) -> bool:
        return bool(self.enabled and self._seal.is_sealed())

    def issue_capability_token(self, caller_id: str, permissions: list[str], ttl_seconds: int = 1800) -> str:
        return self.tokens.issue_token(caller_id, permissions=permissions, ttl_seconds=ttl_seconds)

    def revoke_capability_token(self, token: str) -> dict[str, Any]:
        tok = str(token or "").strip()
        if not tok:
            return {"status": "error", "reason": "empty_token"}
        self.tokens.revoke_token(tok)
        self.record_mode(
            "protect",
            source="shadow:token_revoke",
            ok=True,
            metadata={"event": "token_revoked", "token_prefix": tok[:12]},
        )
        return {"status": "success", "revoked": True, "token_prefix": tok[:12]}

    def bind_recovery_target(
        self,
        target: str,
        write_func: Callable[[str, str, dict[str, Any]], Any],
        hash_exists_func: Callable[[str], bool] | None = None,
    ) -> None:
        self.recovery.bind_target(target, write_func, hash_exists_func)
        self._bound_targets[str(target)] = True

    def _req(self, caller_id: str, request_reason: str, request_token: str | None = None) -> GateRequest:
        token = str(request_token or self._default_tokens.get(caller_id, ""))
        return GateRequest(
            caller_id=str(caller_id),
            request_reason=str(request_reason or "manual"),
            request_token=token,
        )

    def _shadow_usage_bytes(self) -> dict[str, int]:
        return self.capacity_guard.usage()

    def _check_backpressure(self) -> dict[str, Any]:
        eval_out = self.capacity_guard.evaluate()
        usage = dict(eval_out.get("usage", {}))
        ratio = float(eval_out.get("ratio", 0.0) or 0.0)
        mode = str(self._seal.status().get("mode", "active"))
        if self.minimal_survival_enabled and ratio >= self.minimal_survival_enter_pct and mode != "minimal_survival":
            st = self._seal.enter_minimal_survival(reason=f"capacity_ratio:{ratio:.3f}")
            self.record_mode(
                "protect",
                source="shadow:capacity_guard",
                ok=True,
                metadata={"event": "enter_minimal_survival", "ratio": ratio, "usage": usage},
            )
            return {
                "entered": True,
                "status": st,
                "ratio": ratio,
                "usage": usage,
                "stage": eval_out.get("stage", "critical"),
            }
        if mode == "minimal_survival" and ratio <= self.minimal_survival_exit_pct:
            st = self._seal.exit_minimal_survival(reason="capacity_recovered")
            self.record_mode(
                "protect",
                source="shadow:capacity_guard",
                ok=True,
                metadata={"event": "exit_minimal_survival", "ratio": ratio, "usage": usage},
            )
            return {
                "exited": True,
                "status": st,
                "ratio": ratio,
                "usage": usage,
                "stage": eval_out.get("stage", "ok"),
            }
        return {"ratio": ratio, "usage": usage, "mode": mode, "stage": eval_out.get("stage", "ok")}

    def status(self, *, verbose: bool = False, history_limit: int = 50) -> dict[str, Any]:
        st = self._seal.status()
        history = list(st.get("history", []) or [])
        if not verbose:
            keep = max(0, int(history_limit))
            st["history_count"] = len(history)
            if keep > 0:
                st["history"] = history[-keep:]
            else:
                st["history"] = []
        usage = self._shadow_usage_bytes()
        snapshots = self.ledger.list_snapshots(limit=5)
        spool_pending = 0
        spool_oldest_pending_ts = ""
        try:
            rows = self.ledger.read_spool()
            pending_rows = [r for r in rows if isinstance(r, dict) and not bool(r.get("replayed", False))]
            spool_pending = len(pending_rows)
            if pending_rows:
                # Keep the earliest timestamp for backlog age checks.
                ts_values = [str(r.get("ts", "") or "") for r in pending_rows if str(r.get("ts", "") or "")]
                if ts_values:
                    spool_oldest_pending_ts = min(ts_values)
        except (OSError, TypeError, ValueError):
            spool_pending = int(st.get("spool_pending_count", 0) or 0)
            spool_oldest_pending_ts = str(st.get("spool_oldest_pending_ts", "") or "")
        return {
            "enabled": self.enabled,
            "mode": st.get("mode", "active"),
            "sealed": bool(st.get("sealed", False)),
            "seal_level": st.get("seal_level", "hard"),
            "shadow_dir": str(self.shadow_dir),
            "events_file": str(self.ledger.events_file),
            "spool_file": str(self.ledger.spool_file),
            "checkpoint_file": str(self.ledger.checkpoints_file),
            "verify_file": str(self.ledger.verify_file),
            "manifest": st,
            "usage": usage,
            "spool_pending": int(spool_pending),
            "spool_oldest_pending_ts": spool_oldest_pending_ts,
            "snapshots": snapshots,
        }

    def trigger_seal(
        self,
        reason: str,
        level: str = "hard",
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
        bypass_cooldown: bool = False,
    ) -> dict[str, Any]:
        if not self.enabled:
            return self.status()
        if not self._stack_guard_ok():
            return {"status": "rejected", "reason": "stack_guard_blocked"}
        pre = self._seal.mode()

        def _commit(_lease_id: str) -> dict[str, Any]:
            out = self._seal.trigger_seal(reason=reason, level=level)
            self.record_mode(
                "seal",
                source="shadow:auto",
                ok=True,
                metadata={"reason": reason, "seal_level": str(level or "hard").lower()},
            )
            return {
                "status": "success",
                "post_state": str(out.get("mode", self._seal.mode())),
                "result": out,
            }

        cooldown = 30
        reason_l = str(reason or "").strip().lower()
        if bypass_cooldown:
            cooldown = 0
        elif reason_l.startswith("manual") or reason_l.startswith("pressure_test"):
            # Explicit/manual seal intent should not be rate-limited.
            cooldown = 0
        elif self._seal.is_sealed() and str(level).lower() == "hard":
            # Allow fast soft->hard escalation without waiting cooldown.
            cooldown = 0
        gate_out = self.gate.execute(
            op_name="trigger_seal",
            permission="seal:trigger",
            req=self._req(caller_id, request_reason=reason or "trigger_seal", request_token=request_token),
            pre_state=pre,
            callback=_commit,
            cooldown_s=cooldown,
            ttl_s=60,
        )
        if str(gate_out.get("status", "")) == "success" and isinstance(gate_out.get("result"), dict):
            result = dict(gate_out["result"])
            result["operation_id"] = str(gate_out.get("operation_id", ""))
            return result
        return gate_out

    def clear_seal(
        self,
        reason: str = "manual",
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
        confirm: bool = True,
        expected_seal_reason: str = "",
        expected_seal_session_id: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return self.status()
        if not self._stack_guard_ok():
            return {"status": "rejected", "reason": "stack_guard_blocked"}
        if not bool(confirm):
            self.record_mode(
                "protect",
                source="shadow:control_gate",
                ok=False,
                error="clear_seal_rejected",
                metadata={"reason": reason},
            )
            return {"status": "rejected", "reason": "confirm_required"}
        cur = self._seal.status()
        want_reason = str(expected_seal_reason or "").strip()
        want_session = str(expected_seal_session_id or "").strip()
        cur_reason = str(cur.get("reason", "")).strip()
        cur_session = str(cur.get("seal_session_id", "")).strip()
        legacy_no_session = not cur_session
        if not want_reason or ((not want_session) and (not legacy_no_session)):
            self.record_mode(
                "protect",
                source="shadow:control_gate",
                ok=False,
                error="clear_seal_rejected",
                metadata={
                    "reason": "missing_seal_identity",
                    "seal_reason": cur_reason,
                    "seal_session_id": cur_session,
                },
            )
            return {
                "status": "rejected",
                "reason": "seal_identity_required",
                "seal_reason": cur_reason,
                "seal_session_id": cur_session,
            }
        session_mismatch = False
        if not legacy_no_session:
            session_mismatch = want_session != cur_session
        if (want_reason != cur_reason) or session_mismatch:
            self.record_mode(
                "protect",
                source="shadow:control_gate",
                ok=False,
                error="clear_seal_rejected",
                metadata={
                    "reason": "seal_identity_mismatch",
                    "expected_reason": want_reason,
                    "expected_session": want_session,
                    "current_reason": cur_reason,
                    "current_session": cur_session,
                },
            )
            return {
                "status": "rejected",
                "reason": "seal_identity_mismatch",
                "seal_reason": cur_reason,
                "seal_session_id": cur_session,
            }
        pre = self._seal.mode()

        def _commit(_lease_id: str) -> dict[str, Any]:
            out = self._seal.clear_seal(reason=reason)
            self.record_mode("unseal", source="shadow:manual", ok=True, metadata={"reason": reason})
            return {
                "status": "success",
                "post_state": str(out.get("mode", self._seal.mode())),
                "result": out,
            }

        gate_out = self.gate.execute(
            op_name="clear_seal",
            permission="seal:clear",
            req=self._req(caller_id, request_reason=reason or "clear_seal", request_token=request_token),
            pre_state=pre,
            callback=_commit,
            cooldown_s=300,
            ttl_s=60,
        )
        if str(gate_out.get("status", "")) == "success" and isinstance(gate_out.get("result"), dict):
            result = dict(gate_out["result"])
            result["operation_id"] = str(gate_out.get("operation_id", ""))
            return result
        return gate_out

    def handle_write_error(self, reason: str, source: str = "shadow:auto") -> dict[str, Any]:
        if not self.enabled:
            return self.status()
        if not self._seal.is_sealed():
            level = self.auto_seal_on_write_error_level
            return self.trigger_seal(reason=reason, level=level, caller_id="system_bootstrap")

        # Already sealed: increment streak and promote soft -> hard if needed.
        out = self._seal.note_write_error(
            threshold=self.soft_to_hard_error_threshold,
            reason=reason,
        )
        if bool(out.get("promoted_to_hard", False)):
            self.record_mode(
                "seal",
                source=source,
                ok=True,
                metadata={
                    "reason": reason,
                    "seal_level": "hard",
                    "trigger": "soft_to_hard_promotion",
                },
            )
        return out

    def handle_write_success(self) -> None:
        if not self.enabled:
            return
        self._seal.note_write_success()

    def record_data(
        self,
        *,
        action: str,
        source: str,
        content: str = "",
        ok: bool = True,
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {}
        if str(self._seal.mode()) == "minimal_survival" and str(action) == "read":
            self._minimal_read_counter += 1
            every = max(1, int(self.minimal_survival_read_sample_every))
            if (self._minimal_read_counter % every) != 0:
                return {}
        return self.ledger.append_event(
            event_type="data",
            action=action,
            source=source,
            mode=self._seal.mode(),
            ok=ok,
            content=content,
            error=error,
            metadata=metadata or {},
        )

    def record_mode(
        self,
        action: str,
        *,
        source: str,
        ok: bool = True,
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {}
        row = self.ledger.append_event(
            event_type="mode",
            action=action,
            source=source,
            mode=self._seal.mode(),
            ok=ok,
            content="",
            error=error,
            metadata=metadata or {},
        )
        if str(action) in {"seal", "unseal", "recover", "protect", "checkpoint"}:
            emit_system_log(
                f"shadow_{action}",
                {
                    "source": source,
                    "ok": bool(ok),
                    "mode": self._seal.mode(),
                    "error": str(error or ""),
                    "event_id": row.get("event_id", ""),
                },
            )
        return row

    def spool_write(self, content: str, source: str = "shadow:sealed") -> dict[str, Any]:
        if not self.enabled:
            return {}
        bp = self._check_backpressure()
        if str(self._seal.mode()) == "minimal_survival":
            text = str(content or "")
            minimal = text[:200]
            item = self.ledger.append_spool(source, minimal)
            item["minimal_survival"] = True
            item["capacity"] = bp
            self._seal.inc_sealed_writes(1)
            self.record_mode(
                "protect",
                source="shadow:capacity_guard",
                ok=True,
                metadata={
                    "event": "minimal_survival_spool",
                    "spool_id": item.get("spool_id"),
                    "capacity": bp,
                },
            )
            return item
        item = self.ledger.append_spool(source, str(content or ""))
        self._seal.inc_sealed_writes(1)
        self.record_data(
            action="write",
            source=source,
            content=str(content or ""),
            ok=True,
            metadata={"spooled": True, "spool_id": item.get("spool_id")},
        )
        return item

    def should_takeover_write(self, risk: str = "high") -> bool:
        if not self.enabled:
            return False
        if not self._seal.is_sealed():
            return False
        level = self._seal.seal_level()
        risk_norm = str(risk or "high").lower()
        if level == "hard":
            return True
        # soft seal: only divert high-risk writes
        return risk_norm in {"high", "critical"}

    def replay_spool(
        self,
        *,
        target: str = "main_memory",
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        if not self._stack_guard_ok():
            return {"status": "rejected", "reason": "stack_guard_blocked"}
        pre = self._seal.mode()

        def _commit(lease_id: str) -> dict[str, Any]:
            gate = self.checkpoint_guard.verify_gate()
            if not bool(gate.get("ok", False)):
                self.record_mode(
                    "protect",
                    source="shadow:checkpoint_guard",
                    ok=False,
                    error=str(gate.get("reason", "checkpoint_mismatch")),
                    metadata=gate,
                )
                return {
                    "status": "blocked",
                    "post_state": self._seal.mode(),
                    "error": str(gate.get("reason", "checkpoint_mismatch")),
                    "failed": 0,
                    "replayed": 0,
                    "skipped": 0,
                    "remaining": 0,
                }
            out = self.recovery.replay_spool(target=target)
            if not self.locking.validate_lease(lease_id):
                return {
                    "status": "partial",
                    "error": "lease_expired_midflight",
                    "post_state": self._seal.mode(),
                    **out,
                }
            self.record_mode(
                "recover",
                source="shadow:replay_spool",
                ok=(out.get("failed", 0) == 0),
                metadata=out,
            )
            return {
                "status": str(out.get("status", "success")),
                "post_state": self._seal.mode(),
                **out,
            }

        return self.gate.execute(
            op_name="replay_spool",
            permission="shadow:replay",
            req=self._req(caller_id, request_reason=f"replay_to:{target}", request_token=request_token),
            pre_state=pre,
            callback=_commit,
            cooldown_s=0,
            ttl_s=300,
        )

    def recover_from_events(
        self,
        *,
        target: str = "main_memory",
        since_ts: str | None = None,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        if not self._stack_guard_ok():
            return {"status": "rejected", "reason": "stack_guard_blocked"}
        pre = self._seal.mode()

        def _commit(lease_id: str) -> dict[str, Any]:
            gate = self.checkpoint_guard.verify_gate()
            if not bool(gate.get("ok", False)):
                self.record_mode(
                    "protect",
                    source="shadow:checkpoint_guard",
                    ok=False,
                    error=str(gate.get("reason", "checkpoint_mismatch")),
                    metadata=gate,
                )
                return {
                    "status": "blocked",
                    "post_state": self._seal.mode(),
                    "error": str(gate.get("reason", "checkpoint_mismatch")),
                    "failed": 0,
                    "recovered": 0,
                    "skipped": 0,
                    "quarantined": 0,
                }
            out = self.recovery.recover_from_events(target=target, since_ts=since_ts)
            if not self.locking.validate_lease(lease_id):
                return {
                    "status": "partial",
                    "error": "lease_expired_midflight",
                    "post_state": self._seal.mode(),
                    **out,
                }
            self.record_mode(
                "recover",
                source="shadow:recover_events",
                ok=(out.get("failed", 0) == 0),
                metadata=out,
            )
            return {
                "status": str(out.get("status", "success")),
                "post_state": self._seal.mode(),
                **out,
            }

        return self.gate.execute(
            op_name="recover_events",
            permission="shadow:recover",
            req=self._req(caller_id, request_reason=f"recover_to:{target}", request_token=request_token),
            pre_state=pre,
            callback=_commit,
            cooldown_s=0,
            ttl_s=300,
        )

    def verify_checkpoints(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        out = self.ledger.verify_checkpoints()
        if not bool(out.get("ok", False)):
            self._seal.trigger_seal(reason="events_tamper_detected", level="hard")
            self.record_mode(
                "protect",
                source="shadow:verify",
                ok=False,
                error="events_tamper_detected",
                metadata=out,
            )
        try:
            self.ledger.append_verify_result(out)
        except OSError as exc:
            print(f"[ShadowGuard] Failed appending verify result to ledger: {exc}")
        self.record_mode(
            "checkpoint",
            source="shadow:verify",
            ok=bool(out.get("ok", False)),
            metadata=out,
        )
        return out

    def archive_replayed_spool(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        out = self.ledger.archive_replayed_spool(
            hot_days=self.spool_archive_hot_days,
            warm_days=self.spool_archive_warm_days,
            cold_days=self.spool_archive_cold_days,
        )
        self.record_mode(
            "protect",
            source="shadow:archive_spool",
            ok=(str(out.get("status", "")) == "success"),
            metadata=out,
        )
        return out

    def startup_self_heal(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        out = self.ledger.startup_self_heal()
        self.record_mode(
            "protect",
            source="shadow:self_heal",
            ok=(str(out.get("status", "")) == "success"),
            metadata=out,
        )
        return out

    def rotate_events_monthly(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        out = self.ledger.rotate_events_monthly()
        self.record_mode(
            "protect",
            source="shadow:rotate_events_monthly",
            ok=(str(out.get("status", "")) == "success"),
            metadata=out,
        )
        return out

    def run_recovery_drill(
        self,
        *,
        caller_id: str = "trusted_cli",
        request_token: str = "",
        sample_text: str = "shadow_recovery_drill_sample",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        if self.is_sealed():
            return {"status": "blocked", "reason": "sealed_already"}
        captured: list[dict[str, Any]] = []

        def _drill_writer(text: str, source: str, meta: dict[str, Any]) -> None:
            captured.append({"text": str(text or ""), "source": str(source or ""), "meta": dict(meta or {})})

        self.bind_recovery_target("drill_memory", _drill_writer)
        seal_out = self.trigger_seal(
            "shadow_recovery_drill", level="soft", caller_id=caller_id, request_token=request_token
        )
        if not bool(seal_out.get("sealed", False)):
            return {"status": "rejected", "reason": "drill_seal_failed", "seal": seal_out}
        spool = self.spool_write(str(sample_text or "shadow_recovery_drill_sample"), source="shadow:drill")
        replay = self.replay_spool(target="drill_memory", caller_id=caller_id, request_token=request_token)
        ok = int(replay.get("replayed", 0) or 0) >= 1 and len(captured) >= 1
        out = {
            "status": "success" if ok else "partial",
            "seal": seal_out,
            "spool": spool,
            "replay": replay,
            "captured": len(captured),
        }
        self.record_mode(
            "recover",
            source="shadow:recovery_drill",
            ok=ok,
            metadata=out,
        )
        return out

    def sync_verified_backup(
        self,
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        if not self._stack_guard_ok():
            return {"status": "rejected", "reason": "stack_guard_blocked"}
        pre = self._seal.mode()

        def _commit(_lease_id: str) -> dict[str, Any]:
            chk = self.checkpoint_guard.verify_gate()
            m = self._seal.status()
            if (
                not bool(chk.get("ok", False))
                or (not bool(m.get("manifest_signature_valid", True)))
                or bool(self._startup_manifest_untrusted)
            ):
                out_blocked: dict[str, Any] = {
                    "status": "blocked",
                    "reason": "backup_sync_blocked",
                    "checkpoint_ok": bool(chk.get("ok", False)),
                    "manifest_signature_valid": bool(m.get("manifest_signature_valid", True)),
                    "startup_manifest_untrusted": bool(self._startup_manifest_untrusted),
                }
                self.record_mode(
                    "protect",
                    source="shadow:backup_sync",
                    ok=False,
                    error="backup_sync_blocked",
                    metadata=out_blocked,
                )
                return {"status": "blocked", "post_state": self._seal.mode(), **out_blocked}
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = (
                self._seal.status().get("last_recovered_at", "") or self._seal.status().get("sealed_at", "") or "now"
            )
            stamp = str(stamp).replace(":", "").replace("-", "")
            snap_dir = self.backup_dir / f"snapshot_{stamp}"
            snap_dir.mkdir(parents=True, exist_ok=True)
            copied = []
            for p in [
                self.ledger.events_file,
                self.ledger.checkpoints_file,
                self._seal.manifest_file,
            ]:
                if not p.exists():
                    continue
                dst = snap_dir / p.name
                shutil.copy2(p, dst)
                copied.append(str(dst))
            for sp in self.ledger.list_snapshots(limit=10):
                p = Path(str(sp.get("path", "")))
                if not p.exists():
                    continue
                try:
                    shutil.copy2(p, snap_dir / p.name)
                except OSError:
                    continue
            manifest = self.backup_dir / "backup_manifest.json"
            rows = []
            if manifest.exists():
                try:
                    import json

                    rows = json.loads(manifest.read_text(encoding="utf-8"))
                    if not isinstance(rows, list):
                        rows = []
                except (TypeError, ValueError, json.JSONDecodeError, OSError):
                    rows = []
            rows.append(
                {
                    "ts": self._seal.status().get("last_recovered_at", "") or self._seal.status().get("sealed_at", ""),
                    "path": str(snap_dir),
                    "copied": copied,
                }
            )
            tmp = manifest.with_suffix(".tmp")
            import json

            tmp.write_text(json.dumps(rows[-20:], ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, manifest)
            out_success: dict[str, Any] = {
                "status": "success",
                "post_state": self._seal.mode(),
                "backup_dir": str(snap_dir),
                "copied": copied,
            }
            self.record_mode("protect", source="shadow:backup_sync", ok=True, metadata=out_success)
            return out_success

        return self.gate.execute(
            op_name="backup_sync",
            permission="shadow:backup_sync",
            req=self._req(caller_id, request_reason="backup_sync", request_token=request_token),
            pre_state=pre,
            callback=_commit,
            cooldown_s=60,
            ttl_s=120,
        )

    def _is_whitelisted_backup_snapshot(self, path: Path) -> bool:
        try:
            p = path.expanduser().resolve()
            root = self.backup_dir.expanduser().resolve()
        except OSError:
            return False
        if root == p:
            return False
        try:
            p.relative_to(root)
        except ValueError:
            return False
        if not p.parent.name.startswith("snapshot_"):
            return False
        if p.name not in {"shadow_events.jsonl", "shadow_checkpoints.jsonl", "seal_manifest.json"}:
            return False
        return True

    def restore_backup_snapshot(
        self,
        backup_events_path: str,
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        if not self._stack_guard_ok():
            return {"status": "rejected", "reason": "stack_guard_blocked"}
        pre = self._seal.mode()

        def _commit(_lease_id: str) -> dict[str, Any]:
            src = Path(str(backup_events_path or "")).expanduser()
            if (not src.exists()) or (not self._is_whitelisted_backup_snapshot(src)):
                out = {
                    "status": "blocked",
                    "reason": "backup_snapshot_not_whitelisted",
                    "path": str(src),
                    "backup_root": str(self.backup_dir),
                }
                self.record_mode(
                    "protect",
                    source="shadow:restore_backup_snapshot",
                    ok=False,
                    error="backup_snapshot_not_whitelisted",
                    metadata=out,
                )
                return {"post_state": self._seal.mode(), **out}
            verify = self.ledger.verify_snapshot(str(src))
            if not bool(verify.get("ok", False)):
                out_invalid: dict[str, Any] = {
                    "status": "blocked",
                    "reason": "backup_snapshot_invalid",
                    "verify": verify,
                    "path": str(src),
                }
                self.record_mode(
                    "protect",
                    source="shadow:restore_backup_snapshot",
                    ok=False,
                    error="backup_snapshot_invalid",
                    metadata=out_invalid,
                )
                return {"post_state": self._seal.mode(), **out_invalid}
            # Optional sidecars from same snapshot folder
            parent = src.parent
            cp = parent / "shadow_checkpoints.jsonl"
            mf = parent / "seal_manifest.json"
            if cp.exists():
                try:
                    if not self.checkpoint_guard.verify_gate().get("ok", True):
                        # existing live file may be compromised, still allow replacement by validated backup
                        pass
                    tmp_cp = self.ledger.checkpoints_file.with_suffix(".restore.tmp")
                    shutil.copy2(cp, tmp_cp)
                    os.replace(tmp_cp, self.ledger.checkpoints_file)
                except OSError as exc:
                    print(f"[ShadowGuard] Failed restoring checkpoints from backup {cp}: {exc}")
            if mf.exists():
                try:
                    out_mf = self._seal.restore_manifest_snapshot(str(mf))
                    if str(out_mf.get("status", "")) != "success":
                        return {
                            "status": "blocked",
                            "reason": "manifest_restore_failed",
                            "manifest_restore": out_mf,
                            "post_state": self._seal.mode(),
                        }
                except (OSError, ValueError, TypeError) as exc:
                    return {
                        "status": "blocked",
                        "reason": f"manifest_restore_error:{exc}",
                        "post_state": self._seal.mode(),
                    }

            tmp = self.ledger.events_file.with_suffix(".restore.tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, self.ledger.events_file)
            self.ledger._seq = self.ledger._read_last_seq()  # noqa: SLF001
            heal = self.ledger.startup_self_heal()
            out_restored: dict[str, Any] = {
                "status": "success",
                "post_state": self._seal.mode(),
                "restored_from": str(src),
                "verify": verify,
                "heal": heal,
            }
            self.record_mode("protect", source="shadow:restore_backup_snapshot", ok=True, metadata=out_restored)
            return out_restored

        return self.gate.execute(
            op_name="restore_backup_snapshot",
            permission="shadow:restore_backup_snapshot",
            req=self._req(
                caller_id,
                request_reason=f"restore_backup_snapshot:{backup_events_path}",
                request_token=request_token,
            ),
            pre_state=pre,
            callback=_commit,
            cooldown_s=60,
            ttl_s=180,
        )

    # Self-check extension interfaces for future system diagnostics.
    def list_shadow_snapshots(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.ledger.list_snapshots(limit=limit)

    def verify_shadow_snapshot(self, snapshot_path: str) -> dict[str, Any]:
        return self.ledger.verify_snapshot(snapshot_path)

    def restore_shadow_snapshot(
        self,
        snapshot_path: str,
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        if not self._stack_guard_ok():
            return {"status": "rejected", "reason": "stack_guard_blocked"}
        pre = self._seal.mode()

        def _commit(_lease_id: str) -> dict[str, Any]:
            verify = self.ledger.verify_snapshot(snapshot_path)
            if not bool(verify.get("ok", False)):
                return {
                    "status": "blocked",
                    "post_state": self._seal.mode(),
                    "error": str(verify.get("reason", "snapshot_invalid")),
                    "verify": verify,
                }
            src = Path(snapshot_path)
            tmp = self.ledger.events_file.with_suffix(".restore.tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, self.ledger.events_file)
            # rebuild seq and run a quick repair pass
            self.ledger._seq = self.ledger._read_last_seq()  # noqa: SLF001
            heal = self.ledger.startup_self_heal()
            self.record_mode(
                "protect",
                source="shadow:restore_snapshot",
                ok=True,
                metadata={"snapshot_path": str(src), "verify": verify, "heal": heal},
            )
            return {
                "status": "success",
                "post_state": self._seal.mode(),
                "restored_from": str(src),
                "verify": verify,
                "heal": heal,
            }

        return self.gate.execute(
            op_name="restore_snapshot",
            permission="shadow:restore_snapshot",
            req=self._req(
                caller_id,
                request_reason=f"restore_snapshot:{snapshot_path}",
                request_token=request_token,
            ),
            pre_state=pre,
            callback=_commit,
            cooldown_s=60,
            ttl_s=180,
        )

    def list_manifest_snapshots(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._seal.list_manifest_snapshots(limit=limit)

    def restore_manifest_snapshot(
        self,
        snapshot_path: str,
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        if not self._stack_guard_ok():
            return {"status": "rejected", "reason": "stack_guard_blocked"}
        pre = self._seal.mode()

        def _commit(_lease_id: str) -> dict[str, Any]:
            out = self._seal.restore_manifest_snapshot(snapshot_path)
            ok = str(out.get("status", "")) == "success"
            self.record_mode(
                "protect",
                source="shadow:manifest_restore",
                ok=ok,
                error="" if ok else str(out.get("reason", "manifest_restore_failed")),
                metadata=out,
            )
            return {
                "status": "success" if ok else "blocked",
                "post_state": self._seal.mode(),
                **out,
            }

        return self.gate.execute(
            op_name="restore_manifest",
            permission="shadow:manifest_restore",
            req=self._req(
                caller_id,
                request_reason=f"restore_manifest:{snapshot_path}",
                request_token=request_token,
            ),
            pre_state=pre,
            callback=_commit,
            cooldown_s=60,
            ttl_s=120,
        )

    def search_shadow(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        q = str(query or "").strip().lower()
        if not q:
            return []
        out: list[dict[str, Any]] = []
        for row in self.ledger.read_events():
            if str(row.get("event_type", "")) != "data":
                continue
            if str(row.get("action", "")) != "write":
                continue
            blob = f"{row.get('summary', '')} {row.get('source', '')}".lower()
            if q in blob:
                out.append(
                    {
                        "source": str(row.get("source", "shadow")),
                        "title": "shadow_fallback",
                        "content": str(row.get("summary", "")),
                        "date": row.get("ts", ""),
                        "score": 0.05,
                        "search_type": "shadow",
                    }
                )
            if len(out) >= max(1, int(limit)):
                break
        return out

    def health_check(self, *, readonly: bool = True) -> dict[str, Any]:
        checks: dict[str, bool] = {}
        errors: list[str] = []
        try:
            self.shadow_dir.mkdir(parents=True, exist_ok=True)
            checks["shadow_dir_writable"] = self.shadow_dir.exists()
        except OSError as exc:
            checks["shadow_dir_writable"] = False
            errors.append(f"shadow_dir:{exc}")

        if readonly:
            try:
                # Readonly probe should not rely on direct W_OK for ledger file because
                # immutable/chflags protection intentionally makes direct writes unavailable.
                # Real appends can still succeed via ledger mutable->append->immutable flow.
                checks["events_appendable"] = bool(
                    self.ledger.events_file.parent.exists() and os.access(self.ledger.events_file.parent, os.W_OK)
                )
                if self.ledger.events_file.exists():
                    checks["events_appendable"] = bool(
                        checks["events_appendable"] and os.access(self.ledger.events_file, os.R_OK)
                    )
            except OSError as exc:
                checks["events_appendable"] = False
                errors.append(f"events_access:{exc}")
        else:
            try:
                # probe append
                self.ledger.append_event(
                    event_type="protection",
                    action="protect",
                    source="shadow:health",
                    mode=self._seal.mode(),
                    ok=True,
                    content="health_probe",
                    metadata={"probe": True},
                )
                checks["events_appendable"] = True
            except OSError as exc:
                checks["events_appendable"] = False
                errors.append(f"events_append:{exc}")

        try:
            _ = self._seal.status()
            checks["manifest_readable"] = True
        except OSError as exc:
            checks["manifest_readable"] = False
            errors.append(f"manifest:{exc}")

        try:
            self.ledger.payload_dir.mkdir(parents=True, exist_ok=True)
            checks["payload_writable"] = self.ledger.payload_dir.exists()
        except OSError as exc:
            checks["payload_writable"] = False
            errors.append(f"payload:{exc}")

        # basic consistency
        st = self._seal.status()
        mode = str(st.get("mode", "active"))
        sealed = bool(st.get("sealed", False))
        checks["state_consistent"] = (mode == "sealed") == sealed or mode == "recovering"
        if not checks["state_consistent"]:
            errors.append("state_inconsistent")

        report: dict[str, Any] = {
            "ok": all(checks.values()),
            "checks": checks,
            "errors": errors,
            "status": st,
        }
        # Persist unified health/self-check report for external diagnostics.
        try:
            report["generated_at"] = datetime.now(timezone.utc).isoformat()
            health_file = self.shadow_dir / "shadow_health_report_latest.json"
            tmp = health_file.with_suffix(".tmp")

            tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, health_file)
            ensure_shadow_permissions(self.shadow_dir, backup_dir=self.backup_dir)
            report["report_file"] = str(health_file)
        except OSError as exc:
            existing_errors = report.get("errors")
            errors_list: list[Any]
            if isinstance(existing_errors, list):
                errors_list = list(existing_errors)
            else:
                errors_list = []
            errors_list.append(f"report_persist_error:{exc}")
            report["errors"] = errors_list
        return report

    def _encrypt_spool_payload(self, text: str) -> str:
        raw = str(text or "").encode("utf-8")
        try:
            if self._crypto.is_enabled():
                if not self._crypto.is_unlocked():
                    return str(text or "")
                blob = self._crypto.encrypt_before_write(raw, file_type="shadow_spool")
                return base64.b64encode(blob).decode("ascii")
        except (OSError, ValueError, TypeError):
            return str(text or "")
        return str(text or "")

    def _decrypt_spool_payload(self, cipher_text: str) -> str:
        try:
            blob = base64.b64decode(str(cipher_text or "").encode("ascii"))
            if self._crypto.is_enabled():
                if not self._crypto.is_unlocked():
                    return str(cipher_text or "")
                plain = self._crypto.decrypt_after_read(blob, allow_plaintext=True)
                return plain.decode("utf-8", errors="ignore")
        except (ValueError, TypeError, OSError):
            return str(cipher_text or "")
        return str(cipher_text or "")


_SHADOW_SINGLETONS: dict[str, Any] = {}


class NullShadowSystem:
    def __init__(self, reason: str = "shadow_unavailable") -> None:
        self._reason = str(reason or "shadow_unavailable")

    def is_enabled(self) -> bool:
        return False

    def is_sealed(self) -> bool:
        return False

    def should_takeover_write(self, risk: str = "high") -> bool:
        return False

    def status(self, **_kwargs: Any) -> dict[str, Any]:
        return {"enabled": False, "status": "disabled", "reason": self._reason}

    def health_check(self, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "enabled": False, "reason": self._reason}

    def search_shadow(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return []

    def handle_write_success(self) -> None:
        return None

    def __getattr__(self, _name: str):  # noqa: ANN001
        def _noop(*_args, **_kwargs):  # noqa: ANN202
            return {"status": "disabled", "reason": self._reason}

        return _noop


def get_shadow_system(config: dict[str, Any]) -> ShadowSystem | NullShadowSystem:
    sec_cfg = config.get("settings", {}).get("memory", {}).get("security", {}).get("shadow", {})
    memory_dir = Path(config["memory_dir"])
    shadow_dir = Path(sec_cfg.get("shadow_dir", memory_dir / "security" / "shadow_data")).expanduser()
    key = str(shadow_dir.resolve())
    inst = _SHADOW_SINGLETONS.get(key)
    if inst is None:
        try:
            inst = ShadowSystem(config)
        except (OSError, TypeError, ValueError) as exc:
            inst = NullShadowSystem(reason=f"shadow_init_failed:{exc}")
        _SHADOW_SINGLETONS[key] = inst
    return inst


def content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
