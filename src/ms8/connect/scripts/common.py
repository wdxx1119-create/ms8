from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ...paths import get_ms8_home

logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_root() -> Path:
    raw = os.environ.get("OPENCLAW_MEMORY_AUTO_ROOT", str(get_ms8_home() / "connect"))
    root = Path(raw).expanduser()
    if not _is_writable_dir(root):
        root = (Path.cwd() / ".ms8" / "connect").resolve()
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root


def connect_package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def expand(raw: str) -> Path:
    return Path(str(raw or "")).expanduser()


def expand_keep(raw: str) -> str:
    return str(expand(raw))


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        logger.debug("Failed to load YAML %s: %s", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def load_cfg() -> dict[str, Any]:
    return load_yaml(connect_package_root() / "config" / "mcp_config.yaml")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("Failed to read JSON %s: %s", path, exc)
        return {}
    return obj if isinstance(obj, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_audit(line: str) -> None:
    root = connect_root()
    p = root / "logs" / "audit.log"
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{utc_now()} {line}\n")


def choose_python() -> str:
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p:
            return p
    return "python3"


def run(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "cmd": cmd,
    }


def snapshot_config() -> dict[str, Any]:
    return {
        "connect_root": str(connect_root()),
        "package_root": str(connect_package_root()),
        "config": load_cfg(),
    }


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False
