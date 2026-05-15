from __future__ import annotations

from ms8.engine_core.config import get_config
from ms8.engine_core.security import get_crypto_manager


def test_unlock_and_lock_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_WORKSPACE", str(tmp_path))
    cfg = get_config()
    manager = get_crypto_manager(cfg)
    manager.enable_encryption("Passw0rd-Strong")

    manager.lock()
    assert manager.status()["session_state"] == "locked"
    assert manager.unlock("wrong-pass") is False
    assert manager.status()["session_state"] == "locked"
    assert manager.unlock("Passw0rd-Strong") is True
    assert manager.status()["session_state"] == "unlocked"
