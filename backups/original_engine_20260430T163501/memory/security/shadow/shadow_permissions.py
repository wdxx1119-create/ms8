from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def _chmod_safe(path: Path, mode: int) -> bool:
    try:
        os.chmod(path, mode)
        return True
    except Exception:
        return False


def _mode(path: Path) -> int:
    try:
        return int(path.stat().st_mode & 0o777)
    except Exception:
        return -1


def ensure_shadow_permissions(
    shadow_dir: Path,
    *,
    backup_dir: Optional[Path] = None,
    audit_cb: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
) -> Dict[str, Any]:
    shadow_dir.mkdir(parents=True, exist_ok=True)
    targets: List[tuple[Path, int, str, bool]] = [
        (shadow_dir, 0o700, "dir", True),
        (shadow_dir / "spool", 0o700, "dir", True),
        (shadow_dir / "payloads", 0o700, "dir", True),
        (shadow_dir / "snapshots", 0o700, "dir", True),
        (shadow_dir / "quarantine", 0o700, "dir", True),
        (shadow_dir / "shadow_events.jsonl", 0o600, "file", True),
        (shadow_dir / "seal_manifest.json", 0o600, "file", True),
        (shadow_dir / "shadow_checkpoints.jsonl", 0o600, "file", True),
        (shadow_dir / "ops_audit.jsonl", 0o600, "file", True),
        (shadow_dir / "shadow_health_report_latest.json", 0o600, "file", False),
        (shadow_dir / "startup_integrity_emit_state.json", 0o600, "file", False),
        # key file is optional when Keychain is used; do not create if missing.
        (shadow_dir / "manifest_hmac.key", 0o400, "file", False),
    ]
    if backup_dir is not None:
        targets.append((backup_dir, 0o700, "dir", True))
        targets.extend(
            [
                (backup_dir / "shadow_events.jsonl", 0o600, "file", False),
                (backup_dir / "seal_manifest.json", 0o600, "file", False),
                (backup_dir / "backup_manifest.json", 0o600, "file", False),
            ]
        )

    corrected = []
    violations = []
    for path, want_mode, kind, create_if_missing in targets:
        if kind == "dir":
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if create_if_missing and (not path.exists()):
                path.write_text("", encoding="utf-8")
            if not path.exists():
                continue
        got = _mode(path)
        if got != want_mode:
            violations.append({"path": str(path), "got": got, "want": want_mode})
            if audit_cb:
                audit_cb("permission_violation_detected", {"path": str(path), "got": got, "want": want_mode})
            if _chmod_safe(path, want_mode):
                corrected.append({"path": str(path), "mode": want_mode})
                if audit_cb:
                    audit_cb("permission_corrected", {"path": str(path), "mode": want_mode})
    return {
        "status": "success",
        "violations": violations,
        "corrected": corrected,
    }
