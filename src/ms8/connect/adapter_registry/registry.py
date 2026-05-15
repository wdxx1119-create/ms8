from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ms8.connect.scripts.common import connect_root

logger = logging.getLogger(__name__)


def registry_file(base_dir: Path | None = None) -> Path:
    root = base_dir if isinstance(base_dir, Path) else Path(__file__).resolve().parent
    return root / "adapters.json"


def load_registry(base_dir: Path | None = None) -> dict[str, Any]:
    path = registry_file(base_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("Failed to load adapter registry %s: %s", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def save_registry(payload: dict[str, Any], base_dir: Path | None = None) -> Path:
    path = registry_file(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def upsert_adapter(
    key: str,
    *,
    status: str = "active",
    capabilities: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    payload = load_registry(base_dir)
    caps = capabilities if isinstance(capabilities, list) else []
    payload[str(key)] = {
        "status": str(status),
        "capabilities": [str(x) for x in caps],
        "metadata": metadata if isinstance(metadata, dict) else {},
    }
    save_registry(payload, base_dir)
    return payload[str(key)]


class AdapterRegistry:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir if isinstance(base_dir, Path) else Path(__file__).resolve().parent

    def _save(self, payload: dict[str, Any]) -> Path:
        return save_registry(payload, self.base_dir)

    def register(
        self,
        key: str,
        *,
        status: str = "active",
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return upsert_adapter(
            key,
            status=status,
            capabilities=capabilities,
            metadata=metadata,
            base_dir=self.base_dir,
        )

    def list_adapters(self) -> dict[str, Any]:
        return load_registry(self.base_dir)

    def is_write_allowed(self) -> bool:
        try:
            root = connect_root()
            probe = root / "runtime" / ".adapter_registry_probe"
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False
