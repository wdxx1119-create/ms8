"""macOS launchd helper for MS8 watch."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from os import environ
from pathlib import Path

from .runtime import get_runtime_dir

LABEL = "com.ms8.watch"
ABSORB_LABEL = "com.ms8.absorb.watch"
OPENCLAW_LABELS = ("com.openclaw.memory.mcp", "com.openclaw.memory.maintenance")


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _absorb_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{ABSORB_LABEL}.plist"


def _common_env(runtime: Path) -> dict[str, str]:
    return {
        "MS8_HOME": str(runtime),
        "MS8_ENGINE_MODE": environ.get("MS8_ENGINE_MODE", "ms8_core"),
        "MS8_ENGINE_WORKSPACE": environ.get("MS8_ENGINE_WORKSPACE", ""),
        "OPENCLAW_MEMORY_WORKSPACE": str(runtime),
        "OPENCLAW_MEMORY_FAST_START": environ.get("OPENCLAW_MEMORY_FAST_START", "1"),
        "MS8_USE_CORE_WRITE": environ.get("MS8_USE_CORE_WRITE", "1"),
        "MS8_USE_CORE_RETRIEVAL": environ.get("MS8_USE_CORE_RETRIEVAL", "1"),
    }


def _program_arguments(*args: str) -> list[str]:
    ms8_bin = shutil.which("ms8")
    if ms8_bin:
        return [ms8_bin, *args]
    return [sys.executable, "-m", "ms8", *args]


def install_service(interval_seconds: int = 1800) -> dict:
    plist_path = _plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    runtime = get_runtime_dir()
    logs = runtime / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": _program_arguments("watch", "--interval", str(interval_seconds)),
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": _common_env(runtime),
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


def install_absorb_service() -> dict:
    plist_path = _absorb_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    runtime = get_runtime_dir()
    logs = runtime / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": ABSORB_LABEL,
        "ProgramArguments": _program_arguments("absorb", "start"),
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": _common_env(runtime),
        "StandardOutPath": str(logs / "absorb-service.out.log"),
        "StandardErrorPath": str(logs / "absorb-service.err.log"),
    }
    with plist_path.open("wb") as f:
        plistlib.dump(payload, f)

    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, text=True)
    load = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    return {"ok": load.returncode == 0, "plist": str(plist_path), "stderr": load.stderr.strip()}


def remove_absorb_service() -> dict:
    plist_path = _absorb_plist_path()
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
    absorb_running = ABSORB_LABEL in listed.stdout
    return {
        "ok": True,
        "installed": plist_path.exists(),
        "running": running,
        "plist": str(plist_path),
        "absorb_installed": _absorb_plist_path().exists(),
        "absorb_running": absorb_running,
        "absorb_plist": str(_absorb_plist_path()),
        "openclaw_services": openclaw,
    }


def absorb_service_status() -> dict:
    listed = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    plist_path = _absorb_plist_path()
    return {
        "ok": True,
        "installed": plist_path.exists(),
        "running": ABSORB_LABEL in listed.stdout,
        "plist": str(plist_path),
    }
