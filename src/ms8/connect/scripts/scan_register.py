from __future__ import annotations

from ms8.connect.adapter_registry.registry import save_registry
from ms8.connect.adapter_registry.scan_tools import scan_local_tools
from ms8.connect.scripts.common import connect_package_root


def run() -> dict:
    scan = scan_local_tools()
    payload = {
        "ms8_default_adapter": {
            "status": "active",
            "capabilities": ["submit", "query", "context", "status", "profile"],
            "metadata": scan,
        }
    }
    save_registry(payload, connect_package_root() / "adapter_registry")
    return {"ok": True, "registry_entries": len(payload), "scan": scan}


def main() -> dict:
    return run()


if __name__ == "__main__":
    print(main())
