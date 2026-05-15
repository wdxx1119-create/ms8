from __future__ import annotations

from typing import Any, Dict

from .crypto_manager import CryptoManager


def recover_with_recovery_key(
    manager: CryptoManager, recovery_key: str, new_master_password: str
) -> Dict[str, Any]:
    material = manager.km.load_recovery_material()
    if not material:
        return {"status": "error", "reason": "recovery_material_missing"}
    try:
        dek = manager.km.recover_data_key(material, recovery_key)
    except Exception as exc:
        return {"status": "error", "reason": f"invalid_recovery_key:{exc}"}
    kdf_meta = manager.km.create_master_secret(new_master_password)
    wrapped = manager.km.wrap_data_key(dek, new_master_password, kdf_meta)
    manager.km.save_material(wrapped, enabled=True)
    manager._dek = dek  # noqa: SLF001 - recovery flow owns manager session state.
    manager._state["enabled"] = True  # noqa: SLF001
    manager._save_state()  # noqa: SLF001
    return {"status": "success", "message": "recovery_completed", "status_view": manager.status()}

