"""macOS launchd helper for MS8 watch."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from os import environ
from pathlib import Path

from .runtime import get_runtime_dir

LABEL = "com.ms8.watch"
OPENCLAW_LABELS = ("com.openclaw.memory.mcp", "com.openclaw.memory.maintenance")


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def install_service(interval_seconds: int = 1800) -> dict:
    plist_path = _plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    runtime = get_runtime_dir()
    logs = runtime / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "ms8",
            "watch",
            "--interval",
            str(interval_seconds),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": {
            "MS8_HOME": str(runtime),
            "MS8_ENGINE_MODE": environ.get("MS8_ENGINE_MODE", "ms8_core"),
            "MS8_ENGINE_WORKSPACE": environ.get("MS8_ENGINE_WORKSPACE", ""),
            "OPENCLAW_MEMORY_WORKSPACE": str(runtime),
            "OPENCLAW_MEMORY_FAST_START": environ.get("OPENCLAW_MEMORY_FAST_START", "1"),
            "MS8_USE_CORE_WRITE": environ.get("MS8_USE_CORE_WRITE", "1"),
            "MS8_USE_CORE_RETRIEVAL": environ.get("MS8_USE_CORE_RETRIEVAL", "1"),
        },
        "StandardOutPath": str(logs / "service.out.log"),
        "StandardErrorPath": str(logs / "service.err.log"),
    }
    with plist_path.open("wb") as f:
        plistlib.dump(payload, f)

    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, text=True)
    load = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    return {"ok": load.returncode == 0, "plist": str(plist_path), "stderr": load.stderr.strip()}


def remove_service() -> dict:
    plist_path = _plist_path()
    if plist_path.exists():
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, text=True)
        plist_path.unlink()
    return {"ok": True, "plist": str(plist_path)}


def service_status() -> dict:
    plist_path = _plist_path()
    listed = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    running = LABEL in listed.stdout
    openclaw = {}
    for label in OPENCLAW_LABELS:
        openclaw[label] = label in listed.stdout
    return {
        "ok": True,
        "installed": plist_path.exists(),
        "running": running,
        "plist": str(plist_path),
        "openclaw_services": openclaw,
    }
