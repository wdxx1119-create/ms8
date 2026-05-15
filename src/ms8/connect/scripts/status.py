from __future__ import annotations

from pathlib import Path

from ms8.connect.mcp_server.memory_service_interface import MemoryServiceInterface
from ms8.connect.scripts.client_config import selected_targets, target_discovery, target_paths
from ms8.connect.scripts.common import connect_package_root, connect_root, load_yaml


def run_status() -> dict:
    cfg = load_yaml(connect_package_root() / "config" / "mcp_config.yaml")
    svc = MemoryServiceInterface.from_config(cfg)
    return svc.status()


def _tail_steps(path: Path, limit: int = 10) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()[-max(1, limit) :]


def _target_connectivity_status(target: str) -> dict:
    out: dict[str, dict] = {}
    discovery = target_discovery(target)
    for name in selected_targets(target):
        path = target_paths(name)[name]
        out[name] = {
            "config_path": str(path),
            "exists": path.exists(),
            "discovery": discovery.get(name, {}),
            "negotiation": {
                "target": name,
                "degrade_mode": "targeted_profile",
                "fallback": "manual_apply_or_verify",
            },
        }
    return out


def main(target: str = "all") -> dict:
    out = run_status()
    out["audit_tail"] = _tail_steps(connect_root() / "logs" / "audit.log", 8)
    out["target_profiles"] = _target_connectivity_status(target)
    out["target"] = target
    return out


if __name__ == "__main__":
    print(main())
