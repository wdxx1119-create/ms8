from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .shadow_fs_guard import set_immutable, set_mutable
from .shadow_schema import utc_now_iso


class ShadowManifestGuard:
    """HMAC signing + verification guard for seal manifest."""

    def __init__(self, shadow_dir: Path, *, backup_dir: Path | None = None, immutable_enabled: bool = False) -> None:
        self.shadow_dir = shadow_dir
        self.shadow_dir.mkdir(parents=True, exist_ok=True)
        self.key_file = self.shadow_dir / "manifest_hmac.key"
        self.backup_dir = backup_dir
        self.immutable_enabled = bool(immutable_enabled)
        self.snapshots_dir = self.shadow_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._key_source = "unknown"
        self._key = self._load_or_create_key()

    def _keychain_service(self) -> str:
        return "openclaw-memory-shadow"

    def _keychain_account(self) -> str:
        return "manifest-hmac-key"

    def _load_key_from_keychain(self) -> bytes | None:
        try:
            out = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    self._keychain_service(),
                    "-a",
                    self._keychain_account(),
                    "-w",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if out.returncode != 0:
                return None
            val = str(out.stdout or "").strip()
            if not val:
                return None
            return bytes.fromhex(val)
        except (subprocess.SubprocessError, OSError, ValueError):
            return None

    def _save_key_to_keychain(self, key: bytes) -> bool:
        try:
            payload = key.hex()
            # -U update if exists
            out = subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-U",
                    "-s",
                    self._keychain_service(),
                    "-a",
                    self._keychain_account(),
                    "-w",
                    payload,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            return out.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def _load_or_create_key(self) -> bytes:
        kc = self._load_key_from_keychain()
        if kc:
            self._key_source = "keychain"
            try:
                if self.key_file.exists():
                    self.key_file.unlink(missing_ok=True)
            except OSError as exc:
                print(f"[ShadowManifestGuard] Failed removing legacy key file {self.key_file}: {exc}")
            return kc
        if self.key_file.exists():
            try:
                self._key_source = "file"
                raw = self.key_file.read_bytes()
                if len(raw) == 32:
                    return raw
            except OSError as exc:
                print(f"[ShadowManifestGuard] Failed reading key file {self.key_file}: {exc}")
        key = os.urandom(32)
        if self._save_key_to_keychain(key):
            self._key_source = "keychain"
            return key
        tmp = self.key_file.with_suffix(".tmp")
        set_mutable(self.key_file, enabled=self.immutable_enabled)
        tmp.write_bytes(key)
        os.replace(tmp, self.key_file)
        try:
            os.chmod(self.key_file, 0o400)
        except OSError as exc:
            print(f"[ShadowManifestGuard] Failed chmod key file {self.key_file}: {exc}")
        set_immutable(self.key_file, enabled=self.immutable_enabled)
        self._key_source = "file"
        return key

    def _canonical(self, obj: dict[str, Any]) -> bytes:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _sign(self, payload: dict[str, Any]) -> str:
        dig = hmac.new(self._key, self._canonical(payload), hashlib.sha256).hexdigest()
        return dig

    def sign_manifest(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload or {})
        body.pop("_signature", None)
        body["_signature"] = self._sign(body)
        return body

    def verify_manifest(self, obj: dict[str, Any]) -> bool:
        if not isinstance(obj, dict):
            return False
        provided = str(obj.get("_signature", ""))
        if not provided:
            return False
        body = dict(obj)
        body.pop("_signature", None)
        expected = self._sign(body)
        return hmac.compare_digest(provided, expected)

    def write_manifest(self, path: Path, payload: dict[str, Any]) -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            snap = self.snapshots_dir / f"manifest_{utc_now_iso().replace(':', '').replace('-', '')}.json"
            try:
                shutil.copy2(path, snap)
            except OSError as exc:
                print(f"[ShadowManifestGuard] Failed snapshotting manifest {path}: {exc}")
        signed = self.sign_manifest(payload)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(signed, ensure_ascii=False, indent=2), encoding="utf-8")
        set_mutable(path, enabled=self.immutable_enabled)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            print(f"[ShadowManifestGuard] Failed chmod manifest {path}: {exc}")
        set_immutable(path, enabled=self.immutable_enabled)
        if self.backup_dir is not None:
            try:
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                mirror = self.backup_dir / "seal_manifest.json"
                mt = mirror.with_suffix(".tmp")
                mt.write_text(json.dumps(signed, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(mt, mirror)
                os.chmod(mirror, 0o600)
            except OSError as exc:
                print(f"[ShadowManifestGuard] Failed mirroring manifest to backup dir {self.backup_dir}: {exc}")
        return signed

    def read_manifest(self, path: Path) -> tuple[dict[str, Any], bool, str]:
        if not path.exists():
            if self.backup_dir is not None:
                try:
                    mirror = self.backup_dir / "seal_manifest.json"
                    if mirror.exists():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(mirror, path)
                except OSError as exc:
                    print(f"[ShadowManifestGuard] Failed recovering manifest from backup {mirror}: {exc}")
        if not path.exists():
            return {}, True, "missing"
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return {}, False, f"manifest_parse_error:{exc}"
        ok = self.verify_manifest(obj)
        if (not ok) and self._key_source == "keychain" and self.key_file.exists():
            try:
                legacy_key = self.key_file.read_bytes()
                old_key = self._key
                self._key = legacy_key
                if self.verify_manifest(obj):
                    self._save_key_to_keychain(legacy_key)
                    try:
                        self.key_file.unlink(missing_ok=True)
                    except OSError as exc:
                        print(f"[ShadowManifestGuard] Failed removing migrated legacy key file {self.key_file}: {exc}")
                    self._key_source = "keychain"
                    return obj if isinstance(obj, dict) else {}, True, "ok_legacy_key_migrated"
                self._key = old_key
            except OSError as exc:
                print(f"[ShadowManifestGuard] Legacy key migration read failed: {exc}")
        if not ok:
            return obj if isinstance(obj, dict) else {}, False, "manifest_signature_invalid"
        return obj, True, "ok"

    def list_manifest_snapshots(self, limit: int = 20) -> list[dict]:
        out: list[dict] = []
        for p in sorted(self.snapshots_dir.glob("manifest_*.json"), reverse=True)[: max(1, int(limit))]:
            try:
                out.append(
                    {
                        "path": str(p),
                        "size": int(p.stat().st_size),
                        "mtime": float(p.stat().st_mtime),
                    }
                )
            except OSError:
                continue
        return out

    def restore_manifest_snapshot(self, live_path: Path, snapshot_path: str) -> dict[str, Any]:
        p = Path(snapshot_path)
        if not p.exists():
            return {"status": "error", "reason": "snapshot_missing", "path": str(p)}
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            if not self.verify_manifest(obj):
                return {"status": "error", "reason": "snapshot_signature_invalid", "path": str(p)}
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return {"status": "error", "reason": f"snapshot_parse_error:{exc}", "path": str(p)}
        tmp = live_path.with_suffix(".tmp")
        tmp.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        os.replace(tmp, live_path)
        try:
            os.chmod(live_path, 0o600)
        except OSError as exc:
            print(f"[ShadowManifestGuard] Failed chmod restored live manifest {live_path}: {exc}")
        return {"status": "success", "restored_from": str(p), "live_path": str(live_path)}
