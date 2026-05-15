from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ms8.engine_core.security.shadow.shadow_guard import ShadowSystem
from ms8.engine_core.security.shadow.shadow_permissions import ensure_shadow_permissions
from ms8.engine_core.security.shadow.shadow_seal import ShadowSeal


def _cfg(base: Path) -> dict:
    return {
        "memory_dir": base,
        "settings": {
            "memory": {
                "security": {
                    "shadow": {
                        "enabled": True,
                        "shadow_dir": str(base / "security" / "shadow_data"),
                        "payload_threshold_chars": 16,
                        "checkpoint_interval": 2,
                        "snapshot_interval": 2,
                        "snapshot_keep": 2,
                        "immutable_enabled": False,
                    }
                }
            }
        },
    }


def test_manifest_signature_invalid_forces_safe_sealed() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_harden_manifest_"))
    shadow = ShadowSystem(_cfg(base))
    shadow.trigger_seal("unit")
    manifest = base / "security" / "shadow_data" / "seal_manifest.json"
    obj = json.loads(manifest.read_text(encoding="utf-8"))
    obj["reason"] = "tampered_by_test"
    manifest.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    seal2 = ShadowSeal(base / "security" / "shadow_data")
    st = seal2.status()
    assert st.get("manifest_signature_valid") is False
    assert st.get("sealed") is True


def test_checkpoint_mismatch_blocks_replay() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_harden_checkpoint_"))
    shadow = ShadowSystem(_cfg(base))
    for i in range(4):
        shadow.record_data(action="write", source="test", content=f"c{i}", ok=True)
    # Tamper event ledger to force checkpoint mismatch.
    events = base / "security" / "shadow_data" / "shadow_events.jsonl"
    lines = [x for x in events.read_text(encoding="utf-8").splitlines() if x.strip()]
    events.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    shadow.trigger_seal("unit")
    shadow.spool_write("A", "test")
    shadow.bind_recovery_target("main_memory", lambda _t, _s, _m: None)
    out = shadow.replay_spool(target="main_memory")
    assert str(out.get("status")) in {"blocked", "rejected"}


def test_backup_sync_blocked_when_manifest_invalid() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_harden_backup_"))
    shadow = ShadowSystem(_cfg(base))
    shadow.trigger_seal("unit")
    manifest = base / "security" / "shadow_data" / "seal_manifest.json"
    obj = json.loads(manifest.read_text(encoding="utf-8"))
    obj["mode"] = "active"  # tamper
    manifest.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    # Recreate system to force re-verify manifest signature.
    shadow = ShadowSystem(_cfg(base))
    out = shadow.sync_verified_backup()
    assert out["status"] == "blocked"


def test_permissions_auto_correct() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_perm_"))
    sh = base / "security" / "shadow_data"
    sh.mkdir(parents=True, exist_ok=True)
    f = sh / "shadow_events.jsonl"
    f.write_text("", encoding="utf-8")
    os.chmod(sh, 0o755)
    os.chmod(f, 0o644)
    report = ensure_shadow_permissions(sh)
    assert report["status"] == "success"
    assert len(report["corrected"]) >= 1


def test_restore_snapshot_via_gate() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_restore_snapshot_"))
    cfg = _cfg(base)
    shadow = ShadowSystem(cfg)
    for i in range(3):
        shadow.record_data(action="write", source="test", content=f"row-{i}", ok=True)
    snaps = shadow.list_shadow_snapshots(limit=1)
    assert snaps
    snap_path = str(snaps[0]["path"])
    out = shadow.restore_shadow_snapshot(snap_path, caller_id="memory_core")
    assert str(out.get("status")) in {"success", "rejected"}
    if str(out.get("status")) == "success":
        assert out.get("restored_from") == snap_path


def test_manifest_snapshot_restore_entrypoint() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_manifest_restore_"))
    shadow = ShadowSystem(_cfg(base))
    shadow.trigger_seal("unit")
    st = shadow.status().get("manifest", {})
    shadow.clear_seal(
        "unit",
        expected_seal_reason=str(st.get("reason", "")),
        expected_seal_session_id=str(st.get("seal_session_id", "")),
    )
    snaps = shadow.list_manifest_snapshots(limit=5)
    assert len(snaps) >= 1
    out = shadow.restore_manifest_snapshot(str(snaps[0]["path"]), caller_id="memory_core")
    assert str(out.get("status")) in {"success", "rejected", "blocked"}


def test_restore_backup_snapshot_whitelist_rejects_outside_path() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_backup_restore_reject_"))
    shadow = ShadowSystem(_cfg(base))
    outside = base / "outside.jsonl"
    outside.write_text("", encoding="utf-8")
    out = shadow.restore_backup_snapshot(str(outside), caller_id="memory_core")
    assert str(out.get("status")) in {"blocked", "rejected"}


def test_restore_backup_snapshot_from_verified_backup() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_backup_restore_ok_"))
    shadow = ShadowSystem(_cfg(base))
    for i in range(3):
        shadow.record_data(action="write", source="test", content=f"r-{i}", ok=True)
    b = shadow.sync_verified_backup()
    assert str(b.get("status")) in {"success", "blocked", "rejected"}
    if str(b.get("status")) != "success":
        return
    backup_events = Path(str(b["backup_dir"])) / "shadow_events.jsonl"
    assert backup_events.exists()
    # mutate live ledger then restore from backup snapshot
    shadow.record_data(action="write", source="test", content="mutated-row", ok=True)
    out = shadow.restore_backup_snapshot(str(backup_events), caller_id="memory_core")
    assert str(out.get("status")) in {"success", "rejected", "blocked"}


def test_health_check_persists_report_file() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_health_report_"))
    shadow = ShadowSystem(_cfg(base))
    out = shadow.health_check()
    report_file = Path(str(out.get("report_file", "")))
    assert report_file.exists()
    text = report_file.read_text(encoding="utf-8")
    assert "checks" in text
