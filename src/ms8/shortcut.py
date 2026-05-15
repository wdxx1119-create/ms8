"""Desktop shortcut management for MS8."""

from __future__ import annotations

import os
from pathlib import Path


def _desktop_dir() -> Path:
    raw = os.environ.get("MS8_DESKTOP")
    return Path(raw).expanduser() if raw else Path("~/Desktop").expanduser()


def _command_content(command: str) -> str:
    return (
        "#!/bin/zsh\n"
        "set -e\n"
        'if [ -n "$MS8_HOME" ]; then\n'
        "  export MS8_HOME\n"
        "fi\n"
        f"{command}\n"
        "echo\n"
        "read -k '?Press any key to close...'\n"
    )


def install_shortcuts() -> dict:
    desktop = _desktop_dir()
    desktop.mkdir(parents=True, exist_ok=True)

    main = desktop / "MS8.command"
    doctor = desktop / "MS8-Doctor.command"

    main.write_text(_command_content("ms8 dashboard"), encoding="utf-8")
    doctor.write_text(_command_content("ms8 doctor"), encoding="utf-8")

    main.chmod(0o755)
    doctor.chmod(0o755)

    return {"ok": True, "desktop": str(desktop), "files": [str(main), str(doctor)]}


def remove_shortcuts() -> dict:
    desktop = _desktop_dir()
    files = [desktop / "MS8.command", desktop / "MS8-Doctor.command"]
    removed = []
    for p in files:
        if p.exists():
            p.unlink()
            removed.append(str(p))
    return {"ok": True, "desktop": str(desktop), "removed": removed}


def shortcut_status() -> dict:
    desktop = _desktop_dir()
    main = desktop / "MS8.command"
    doctor = desktop / "MS8-Doctor.command"
    return {
        "ok": True,
        "desktop": str(desktop),
        "main_exists": main.exists(),
        "doctor_exists": doctor.exists(),
    }


def ensure_shortcuts_once() -> None:
    # Best-effort auto-setup. Never fail user command flow.
    if os.environ.get("MS8_SHORTCUT_AUTO", "1") == "0":
        return
    status = shortcut_status()
    if status["main_exists"] and status["doctor_exists"]:
        return
    try:
        install_shortcuts()
    except OSError:
        return
