from __future__ import annotations

from pathlib import Path

from ms8.engine_core.security.shadow.shadow_guard import ShadowSystem


def _config(tmp_path: Path):
    return {
        "memory_dir": tmp_path / "memory",
        "workspace_dir": tmp_path / "ws",
        "settings": {
            "memory": {
                "security": {
                    "shadow": {
                        "enabled": False,
                        "shadow_dir": str(tmp_path / "shadow"),
                        "backup_dir": str(tmp_path / "shadow_backup"),
                        "immutable_enabled": False,
                        "stack_guard_enabled": False,
                        "auto_self_heal_on_startup": False,
                    }
                }
            }
        },
    }


def test_shadow_guard_disabled_fast_paths(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)

    assert system.is_enabled() is False
    assert system.is_sealed() is False
    assert system.trigger_seal("r")["enabled"] is False
    assert system.clear_seal()["enabled"] is False
    assert system.handle_write_error("e")["enabled"] is False
    system.handle_write_success()
    assert system.record_data(action="write", source="x", content="c") == {}
    assert system.record_mode("seal", source="x") == {}
    assert system.spool_write("abc") == {}
    assert system.should_takeover_write("high") is False
    assert system.replay_spool()["status"] == "disabled"
    assert system.recover_from_events()["status"] == "disabled"
    assert system.verify_checkpoints()["status"] == "disabled"
    assert system.archive_replayed_spool()["status"] == "disabled"
    assert system.startup_self_heal()["status"] == "disabled"
    assert system.rotate_events_monthly()["status"] == "disabled"
    assert system.run_recovery_drill()["status"] == "disabled"
    assert system.sync_verified_backup()["status"] == "disabled"
    assert system.restore_backup_snapshot(str(tmp_path / "x"))["status"] == "disabled"

