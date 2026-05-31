from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.security.shadow import shadow_manifest_guard as mod


class _Proc:
    def __init__(self, code: int = 0, out: str = "") -> None:
        self.returncode = code
        self.stdout = out


def test_key_from_keychain_and_verify_manifest(tmp_path: Path, monkeypatch) -> None:
    key = b"\x11" * 32

    def _run(*args, **kwargs):  # noqa: ANN002, ANN003
        argv = args[0]
        if "find-generic-password" in argv:
            return _Proc(0, key.hex())
        return _Proc(0, "")

    monkeypatch.setattr(mod.subprocess, "run", _run)
    guard = mod.ShadowManifestGuard(tmp_path / "shadow")
    assert guard._key_source == "keychain"
    body = guard.sign_manifest({"a": 1})
    assert guard.verify_manifest(body) is True


def test_fallback_to_file_key_when_keychain_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod.ShadowManifestGuard, "_load_key_from_keychain", lambda self: None)
    monkeypatch.setattr(mod.ShadowManifestGuard, "_save_key_to_keychain", lambda self, key: False)
    guard = mod.ShadowManifestGuard(tmp_path / "shadow")
    assert guard._key_source == "file"
    assert (tmp_path / "shadow" / "manifest_hmac.key").exists()


def test_read_manifest_missing_then_backup_recover(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod.ShadowManifestGuard, "_load_key_from_keychain", lambda self: None)
    monkeypatch.setattr(mod.ShadowManifestGuard, "_save_key_to_keychain", lambda self, key: False)
    shadow = tmp_path / "shadow"
    backup = tmp_path / "backup"
    guard = mod.ShadowManifestGuard(shadow, backup_dir=backup)
    live = shadow / "seal_manifest.json"

    missing_obj, missing_ok, missing_reason = guard.read_manifest(live)
    assert missing_obj == {}
    assert missing_ok is True
    assert missing_reason == "missing"

    signed = guard.sign_manifest({"x": 1})
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "seal_manifest.json").write_text(json.dumps(signed), encoding="utf-8")
    obj, ok, reason = guard.read_manifest(live)
    assert ok is True
    assert reason == "ok"
    assert obj.get("x") == 1
    assert live.exists()


def test_read_manifest_parse_error_and_invalid_signature(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod.ShadowManifestGuard, "_load_key_from_keychain", lambda self: None)
    monkeypatch.setattr(mod.ShadowManifestGuard, "_save_key_to_keychain", lambda self, key: False)
    guard = mod.ShadowManifestGuard(tmp_path / "shadow")
    live = tmp_path / "shadow" / "seal_manifest.json"

    live.write_text("{bad", encoding="utf-8")
    obj, ok, reason = guard.read_manifest(live)
    assert obj == {}
    assert ok is False
    assert reason.startswith("manifest_parse_error:")

    live.write_text(json.dumps({"x": 1, "_signature": "nope"}), encoding="utf-8")
    obj2, ok2, reason2 = guard.read_manifest(live)
    assert ok2 is False
    assert reason2 == "manifest_signature_invalid"
    assert obj2.get("x") == 1


def test_legacy_key_migration_success(tmp_path: Path, monkeypatch) -> None:
    old_key = b"\x33" * 32
    new_key = b"\x44" * 32

    monkeypatch.setattr(mod.ShadowManifestGuard, "_load_key_from_keychain", lambda self: new_key)
    saved: list[bytes] = []
    monkeypatch.setattr(mod.ShadowManifestGuard, "_save_key_to_keychain", lambda self, key: saved.append(key) or True)

    shadow = tmp_path / "shadow"
    guard = mod.ShadowManifestGuard(shadow)
    live = shadow / "seal_manifest.json"

    # sign with old legacy file key
    body = {"m": "legacy"}
    legacy_sig = mod.hmac.new(old_key, guard._canonical(body), mod.hashlib.sha256).hexdigest()
    payload = dict(body)
    payload["_signature"] = legacy_sig
    live.write_text(json.dumps(payload), encoding="utf-8")
    (shadow / "manifest_hmac.key").write_bytes(old_key)

    obj, ok, reason = guard.read_manifest(live)
    assert ok is True
    assert reason == "ok_legacy_key_migrated"
    assert obj["m"] == "legacy"
    assert saved and saved[-1] == old_key


def test_write_manifest_snapshot_and_mirror_error_tolerant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod.ShadowManifestGuard, "_load_key_from_keychain", lambda self: None)
    monkeypatch.setattr(mod.ShadowManifestGuard, "_save_key_to_keychain", lambda self, key: False)
    guard = mod.ShadowManifestGuard(tmp_path / "shadow", backup_dir=tmp_path / "backup")
    live = tmp_path / "shadow" / "seal_manifest.json"
    live.write_text("{}", encoding="utf-8")

    # force backup write failure path
    original_replace = mod.os.replace

    def _replace(src, dst):  # noqa: ANN001
        if str(dst).endswith("/backup/seal_manifest.json"):
            raise OSError("mirror-fail")
        return original_replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", _replace)
    signed = guard.write_manifest(live, {"k": "v"})
    assert signed["k"] == "v"
    snaps = guard.list_manifest_snapshots(limit=5)
    assert isinstance(snaps, list)
    assert snaps


def test_restore_manifest_snapshot_invalid_and_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod.ShadowManifestGuard, "_load_key_from_keychain", lambda self: None)
    monkeypatch.setattr(mod.ShadowManifestGuard, "_save_key_to_keychain", lambda self, key: False)
    guard = mod.ShadowManifestGuard(tmp_path / "shadow")
    live = tmp_path / "shadow" / "seal_manifest.json"

    out_missing = guard.restore_manifest_snapshot(live, str(tmp_path / "none.json"))
    assert out_missing["status"] == "error"
    assert out_missing["reason"] == "snapshot_missing"

    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    out_parse = guard.restore_manifest_snapshot(live, str(bad))
    assert out_parse["status"] == "error"
    assert out_parse["reason"].startswith("snapshot_parse_error:")

    unsigned = tmp_path / "unsigned.json"
    unsigned.write_text(json.dumps({"x": 1}), encoding="utf-8")
    out_sig = guard.restore_manifest_snapshot(live, str(unsigned))
    assert out_sig["status"] == "error"
    assert out_sig["reason"] == "snapshot_signature_invalid"

    signed = guard.sign_manifest({"ok": True})
    good = tmp_path / "good.json"
    good.write_text(json.dumps(signed), encoding="utf-8")
    out_ok = guard.restore_manifest_snapshot(live, str(good))
    assert out_ok["status"] == "success"
    assert live.exists()
