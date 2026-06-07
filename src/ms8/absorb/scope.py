"""Authorization scope management for local file absorption."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..paths import get_config_dir

CONFIG_VERSION = 1
AUTO_WRITE_TIERS = ("OFF", "SUMMARY_ONLY", "LOW_RISK_CHUNKS", "REVIEWED_ONLY")
SYSTEM_ROOTS = (
    Path("/System"),
    Path("/Library"),
    Path("/bin"),
    Path("/sbin"),
    Path("/usr"),
)
DEFAULT_EXCLUDES = (
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "dist",
    "build",
    "vendor",
    ".cache",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def absorb_config_path() -> Path:
    return get_config_dir() / "absorb.json"


def canonicalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _default_config() -> dict[str, Any]:
    now = _now()
    return {
        "version": CONFIG_VERSION,
        "allowed_roots": [],
        "exclude_patterns": list(DEFAULT_EXCLUDES),
        "auto_submit_summaries": False,
        "auto_write_tier": "OFF",
        "created_at": now,
        "updated_at": now,
    }


def load_absorb_config() -> dict[str, Any]:
    path = absorb_config_path()
    if not path.exists():
        return _default_config()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_config()
    if not isinstance(payload, dict):
        return _default_config()
    cfg = _default_config()
    cfg.update(payload)
    cfg["allowed_roots"] = [str(canonicalize_path(p)) for p in cfg.get("allowed_roots", []) if str(p).strip()]
    excludes = cfg.get("exclude_patterns", [])
    cfg["exclude_patterns"] = [str(p) for p in excludes if str(p).strip()]
    tier = str(cfg.get("auto_write_tier") or "").upper()
    if tier not in AUTO_WRITE_TIERS:
        tier = "SUMMARY_ONLY" if bool(cfg.get("auto_submit_summaries", False)) else "OFF"
    cfg["auto_write_tier"] = tier
    cfg["auto_submit_summaries"] = tier in {"SUMMARY_ONLY", "LOW_RISK_CHUNKS"}
    return cfg


def save_absorb_config(config: dict[str, Any]) -> dict[str, Any]:
    path = absorb_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _default_config()
    payload.update(config)
    payload["version"] = CONFIG_VERSION
    payload["updated_at"] = _now()
    if not payload.get("created_at"):
        payload["created_at"] = payload["updated_at"]
    payload["allowed_roots"] = [str(canonicalize_path(p)) for p in payload.get("allowed_roots", [])]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def is_high_risk_path(path: str | Path) -> bool:
    p = canonicalize_path(path)
    home = Path.home().resolve()
    if p == Path("/") or p == home:
        return True
    if p == Path("/private"):
        return True
    if p in {home / "Documents", home / "Desktop", home / "Downloads"}:
        return True
    return any(p == root or root in p.parents for root in SYSTEM_ROOTS)


def add_allowed_root(path: str | Path, *, confirm_high_risk: bool = False) -> dict[str, Any]:
    root = canonicalize_path(path)
    if is_high_risk_path(root) and not confirm_high_risk:
        return {"ok": False, "status": "rejected", "reason": "high_risk_path_requires_confirmation", "path": str(root)}
    cfg = load_absorb_config()
    roots = list(dict.fromkeys([*cfg.get("allowed_roots", []), str(root)]))
    cfg["allowed_roots"] = roots
    save_absorb_config(cfg)
    return {"ok": True, "status": "added", "path": str(root)}


def remove_allowed_root(path: str | Path) -> dict[str, Any]:
    root = str(canonicalize_path(path))
    cfg = load_absorb_config()
    before = list(cfg.get("allowed_roots", []))
    cfg["allowed_roots"] = [p for p in before if p != root]
    save_absorb_config(cfg)
    return {"ok": True, "status": "removed" if root in before else "not_found", "path": root}


def list_allowed_roots() -> list[str]:
    return list(load_absorb_config().get("allowed_roots", []))


def add_exclude_pattern(pattern: str) -> dict[str, Any]:
    pat = str(pattern or "").strip()
    if not pat:
        return {"ok": False, "status": "rejected", "reason": "empty_pattern"}
    cfg = load_absorb_config()
    cfg["exclude_patterns"] = list(dict.fromkeys([*cfg.get("exclude_patterns", []), pat]))
    save_absorb_config(cfg)
    return {"ok": True, "status": "added", "pattern": pat}


def set_auto_submit_summaries(enabled: bool) -> dict[str, Any]:
    tier = "SUMMARY_ONLY" if enabled else "OFF"
    return set_auto_write_tier(tier)


def auto_submit_summaries_enabled() -> bool:
    return auto_write_tier() in {"SUMMARY_ONLY", "LOW_RISK_CHUNKS"}


def set_auto_write_tier(tier: str) -> dict[str, Any]:
    normalized = str(tier or "").upper()
    if normalized not in AUTO_WRITE_TIERS:
        return {"ok": False, "status": "rejected", "reason": "invalid_auto_write_tier", "valid_tiers": list(AUTO_WRITE_TIERS)}
    cfg = load_absorb_config()
    cfg["auto_write_tier"] = normalized
    cfg["auto_submit_summaries"] = normalized in {"SUMMARY_ONLY", "LOW_RISK_CHUNKS"}
    saved = save_absorb_config(cfg)
    return {"ok": True, "auto_write_tier": saved["auto_write_tier"], "auto_submit_summaries": bool(saved["auto_submit_summaries"])}


def auto_write_tier() -> str:
    return str(load_absorb_config().get("auto_write_tier", "OFF") or "OFF").upper()


def _is_hidden_segment(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    return any(part.startswith(".") for part in rel_parts if part not in {"", "."})


def is_path_allowed(path: str | Path) -> bool:
    p = canonicalize_path(path)
    if any(p == root or root in p.parents for root in SYSTEM_ROOTS):
        return False
    cfg = load_absorb_config()
    excludes = set(cfg.get("exclude_patterns", []))
    for raw_root in cfg.get("allowed_roots", []):
        root = canonicalize_path(raw_root)
        if p == root or root in p.parents:
            if _is_hidden_segment(p, root):
                return False
            if any(part in excludes for part in p.parts):
                return False
            return True
    return False
