"""First-run onboarding for MS8."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .runtime import get_runtime_dir
from .shortcut import install_shortcuts


def _marker_path() -> Path:
    root = get_runtime_dir()
    health = root / "health"
    health.mkdir(parents=True, exist_ok=True)
    return health / "onboarding.json"


def onboarding_status() -> dict:
    marker = _marker_path()
    if not marker.exists():
        return {"done": False}
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"done": False}
    return {"done": bool(data.get("done")), "completed_at": data.get("completed_at")}


def run_onboarding() -> dict:
    status = onboarding_status()
    marker_path = _marker_path()
    if status.get("done"):
        existing = {}
        try:
            existing = json.loads(marker_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        if isinstance(existing, dict) and "connect_bootstrap_ok" in existing:
            return {"ok": True, "skipped": True}
        # Upgrade older onboarding marker to include connect bootstrap status.
        existing_done = bool(existing.get("done", True))
        existing_completed = str(existing.get("completed_at") or datetime.now(timezone.utc).isoformat())
        existing_runtime = str(existing.get("runtime") or get_runtime_dir())
        existing_shortcut = bool(existing.get("shortcut_created", False))
        upgraded = _build_marker(existing_done, existing_completed, existing_runtime, existing_shortcut)
        marker_path.write_text(json.dumps(upgraded, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "skipped": False, "upgraded": True, "marker": upgraded}

    root = get_runtime_dir()
    for p in (root, root / "data", root / "backups", root / "logs", root / "health"):
        p.mkdir(parents=True, exist_ok=True)
    shortcut_result = {"ok": False}
    if os.environ.get("MS8_SHORTCUT_AUTO", "1") != "0":
        try:
            shortcut_result = install_shortcuts()
        except OSError:
            shortcut_result = {"ok": False}

    bootstrap_report: dict | None = None
    if os.environ.get("MS8_CONNECT_AUTO", "1") != "0":
        try:
            from .connect.scripts.bootstrap import run_bootstrap

            bootstrap_report = run_bootstrap(
                target=os.environ.get("MS8_CONNECT_TARGET", "all"),
                auto_fix=True,
                silent=True,
            )
        except (ImportError, OSError, RuntimeError) as exc:
            print(f"[Onboarding] Connect bootstrap skipped due to runtime error: {exc}")

    marker = _build_marker(
        done=True,
        completed_at=datetime.now(timezone.utc).isoformat(),
        runtime=str(root),
        shortcut_created=bool(shortcut_result.get("ok")),
        connect_bootstrap_result=bootstrap_report,
    )
    _marker_path().write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "skipped": False, "marker": marker}


def _run_connect_bootstrap() -> dict:
    connect_result = {"ok": False, "skipped": True, "reason": "disabled"}
    if os.environ.get("MS8_CONNECT_AUTO", "1") == "0":
        return connect_result
    connect_result = {"ok": False, "skipped": False}
    try:
        from .connect.scripts.bootstrap import run_bootstrap

        report = run_bootstrap(
            target=os.environ.get("MS8_CONNECT_TARGET", "all"),
            auto_fix=True,
            silent=True,
        )
        connect_result = {
            "ok": bool(report.get("ok", False)),
            "skipped": False,
            "overall_ok": bool(report.get("ok", False)),
            "steps": [str(s.get("name", "")) for s in report.get("steps", []) if isinstance(s, dict)],
        }
    except (ImportError, OSError, RuntimeError) as exc:
        connect_result = {"ok": False, "skipped": False, "error": str(exc)}
    return connect_result


def _build_marker(
    done: bool,
    completed_at: str,
    runtime: str,
    shortcut_created: bool,
    connect_bootstrap_result: dict | None = None,
) -> dict:
    connect_result = (
        connect_bootstrap_result if isinstance(connect_bootstrap_result, dict) else _run_connect_bootstrap()
    )
    return {
        "done": bool(done),
        "completed_at": str(completed_at),
        "runtime": str(runtime),
        "shortcut_created": bool(shortcut_created),
        "connect_bootstrap_ok": bool(connect_result.get("ok", False)),
        "connect_bootstrap": connect_result,
    }
