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
                        "enabled": True,
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


def test_shadow_guard_status_and_tokens(tmp_path):
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)

    st = system.status(verbose=False, history_limit=1)
    assert st["enabled"] is True
    assert "spool_pending" in st
    assert "usage" in st

    tok = system.issue_capability_token("tester", ["shadow:verify"], ttl_seconds=10)
    assert isinstance(tok, str) and tok
    revoked = system.revoke_capability_token(tok)
    assert revoked["status"] == "success"
    assert system.revoke_capability_token("")["status"] == "error"


def test_shadow_guard_trigger_and_clear_paths(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)

    # stack guard rejected path
    monkeypatch.setattr(system, "_stack_guard_ok", lambda: False)
    rejected = system.trigger_seal("manual_test")
    assert rejected["status"] == "rejected"

    # clear seal needs confirm
    rejected_clear = system.clear_seal(confirm=False)
    assert rejected_clear["status"] == "rejected"

    # gate success path by mocking gate + stack guard
    monkeypatch.setattr(system, "_stack_guard_ok", lambda: True)
    monkeypatch.setattr(
        system.gate,
        "execute",
        lambda **_kwargs: {"status": "success", "operation_id": "op1", "result": {"status": "success"}},
    )
    ok = system.trigger_seal("manual_test")
    assert ok["status"] == "success"
    assert ok["operation_id"] == "op1"


def test_shadow_guard_backpressure_branches(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)

    monkeypatch.setattr(system.capacity_guard, "evaluate", lambda: {"ratio": 0.99, "usage": {"shadow": 1}})
    monkeypatch.setattr(system._seal, "status", lambda: {"mode": "active"})
    monkeypatch.setattr(system._seal, "enter_minimal_survival", lambda reason="": {"mode": "minimal_survival"})
    entered = system._check_backpressure()
    assert entered.get("entered") is True

    monkeypatch.setattr(system.capacity_guard, "evaluate", lambda: {"ratio": 0.1, "usage": {"shadow": 1}})
    monkeypatch.setattr(system._seal, "status", lambda: {"mode": "minimal_survival"})
    monkeypatch.setattr(system._seal, "exit_minimal_survival", lambda reason="": {"mode": "active"})
    exited = system._check_backpressure()
    assert exited.get("exited") is True

