from __future__ import annotations

from ms8.engine_core.config import get_config
from ms8.engine_core.security import get_crypto_manager
from ms8.engine_core.security.recovery import recover_with_recovery_key


def test_recovery_key_can_reset_master_password(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_WORKSPACE", str(tmp_path))
    cfg = get_config()
    manager = get_crypto_manager(cfg)
    enabled = manager.enable_encryption("OldPassw0rd!")
    recovery_key = str(enabled["recovery_key"])
    manager.lock()

    out = recover_with_recovery_key(manager, recovery_key, "NewPassw0rd!")
    assert out["status"] == "success"

    manager.lock()
    assert manager.unlock("OldPassw0rd!") is False
    assert manager.unlock("NewPassw0rd!") is True
