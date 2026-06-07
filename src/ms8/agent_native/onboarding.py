"""Onboarding and file generation for agent-native."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from ..paths import get_ms8_home
from .permission import PROFILES, build_policy
from .task_spec import TASK_FILE_MAP, TASK_VERSION
from .task_templates import ABSORB_TASK, CHECK_TASK, INSTALL_TASK, OPS_TASK, README_AGENT, REPORT_TASK, USAGE_TASK


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _project_agent_dir(cwd: Path) -> Path:
    return cwd / ".ms8" / "agent_native"


def _global_policy_path() -> Path:
    return get_ms8_home() / "agent_native" / "agent_policy.json"


def _legacy_policy_path() -> Path:
    return Path.home() / ".ms8_runtime" / "agent_native" / "agent_policy.json"


def migrate_policy_path(*, dry_run: bool = False, force: bool = False, cleanup_legacy: bool = False) -> dict:
    canonical = _global_policy_path()
    legacy = _legacy_policy_path()
    if not legacy.exists():
        return {
            "ok": True,
            "status": "SKIPPED",
            "reason": "legacy_policy_missing",
            "canonical_policy_path": str(canonical),
            "legacy_policy_path": str(legacy),
        }
    if canonical.exists() and not force:
        return {
            "ok": True,
            "status": "SKIPPED",
            "reason": "canonical_exists_use_force",
            "canonical_policy_path": str(canonical),
            "legacy_policy_path": str(legacy),
        }
    if dry_run:
        return {
            "ok": True,
            "status": "PASS",
            "dry_run": True,
            "canonical_policy_path": str(canonical),
            "legacy_policy_path": str(legacy),
            "action": "copy_legacy_to_canonical",
            "cleanup_legacy": bool(cleanup_legacy),
        }
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if canonical.exists():
        bak = canonical.with_suffix(canonical.suffix + f".bak.{_now()}")
        shutil.copy2(canonical, bak)
    shutil.copy2(legacy, canonical)
    removed_legacy = False
    if cleanup_legacy:
        try:
            legacy.unlink(missing_ok=True)
            removed_legacy = True
        except OSError:
            removed_legacy = False
    return {
        "ok": True,
        "status": "PASS",
        "canonical_policy_path": str(canonical),
        "legacy_policy_path": str(legacy),
        "migrated": True,
        "cleanup_legacy": bool(cleanup_legacy),
        "legacy_removed": removed_legacy,
    }


def _write_with_backup(path: Path, content: str, force: bool) -> tuple[bool, str]:
    if path.exists() and not force:
        new_path = path.with_suffix(path.suffix + ".new")
        new_path.write_text(content, encoding="utf-8")
        return (False, str(new_path))
    if path.exists():
        bak = path.with_suffix(path.suffix + f".bak.{_now()}")
        shutil.copy2(path, bak)
    path.write_text(content, encoding="utf-8")
    return (True, str(path))


def init_agent_native(
    profile: str,
    cwd: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    confirm: bool = False,
) -> dict:
    prof = str(profile).strip().upper()
    if prof not in PROFILES:
        return {"ok": False, "status": "FAIL", "reason": f"unsupported_profile:{profile}"}
    if prof == "TRUSTED_AGENT" and not confirm:
        return {"ok": False, "status": "NEEDS_CONFIRM", "reason": "trusted_requires_confirm"}
    project_dir = _project_agent_dir(cwd)
    policy_path = _global_policy_path()
    files = []
    if dry_run:
        return {
            "ok": True,
            "status": "PASS",
            "dry_run": True,
            "permission_profile": prof,
            "policy_path": str(policy_path),
            "generated_files": [
                str(project_dir / TASK_FILE_MAP["install"]),
                str(project_dir / TASK_FILE_MAP["ops"]),
                str(project_dir / TASK_FILE_MAP["check"]),
                str(project_dir / TASK_FILE_MAP["report"]),
                str(project_dir / TASK_FILE_MAP["usage"]),
                str(project_dir / TASK_FILE_MAP["absorb"]),
                str(project_dir / "README_AGENT.md"),
            ],
        }
    project_dir.mkdir(parents=True, exist_ok=True)
    policy_path.parent.mkdir(parents=True, exist_ok=True)

    policy = build_policy(prof, __version__, agent_id="default")
    if policy_path.exists():
        old = json.loads(policy_path.read_text(encoding="utf-8"))
        old_p = str(old.get("permission_profile", "")).upper()
        if old_p != prof and prof == "TRUSTED_AGENT" and not confirm:
            return {"ok": False, "status": "NEEDS_CONFIRM", "reason": "upgrade_requires_confirm"}
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    files.append(str(policy_path))

    mapping = {
        TASK_FILE_MAP["install"]: INSTALL_TASK,
        TASK_FILE_MAP["ops"]: OPS_TASK,
        TASK_FILE_MAP["check"]: CHECK_TASK,
        TASK_FILE_MAP["report"]: REPORT_TASK,
        TASK_FILE_MAP["usage"]: USAGE_TASK,
        TASK_FILE_MAP["absorb"]: ABSORB_TASK,
        "README_AGENT.md": README_AGENT,
    }
    for rel, content in mapping.items():
        _, out = _write_with_backup(project_dir / rel, content, force=force)
        files.append(out)
    return {
        "ok": True,
        "status": "PASS",
        "permission_profile": prof,
        "policy_path": str(policy_path),
        "generated_files": files,
    }


def list_tasks(cwd: Path) -> dict:
    project_dir = _project_agent_dir(cwd)
    if not project_dir.exists():
        return {"ok": False, "status": "MISSING", "tasks": []}
    tasks = []
    for name, fn in TASK_FILE_MAP.items():
        if (project_dir / fn).exists():
            tasks.append(name)
    return {"ok": True, "status": "PASS", "tasks": tasks}


def show_task(cwd: Path, name: str) -> dict:
    project_dir = _project_agent_dir(cwd)
    if not project_dir.exists():
        return {"ok": False, "status": "MISSING", "reason": "run_agent_init_first"}
    key = str(name).strip().lower()
    if key not in TASK_FILE_MAP:
        return {"ok": False, "status": "FAIL", "reason": f"unknown_task:{name}"}
    p = project_dir / TASK_FILE_MAP[key]
    if not p.exists():
        return {"ok": False, "status": "MISSING", "reason": "task_file_missing"}
    return {"ok": True, "status": "PASS", "task": key, "content": p.read_text(encoding="utf-8")}


def verify_tasks(cwd: Path) -> dict:
    project_dir = _project_agent_dir(cwd)
    if not project_dir.exists():
        return {"ok": False, "status": "MISSING", "error_code": "E_TASK_DIR_MISSING", "details": []}
    details: list[dict] = []
    ok = True
    for name, fn in TASK_FILE_MAP.items():
        p = project_dir / fn
        item = {"task": name, "file": str(p), "exists": p.exists(), "version_ok": False}
        if not p.exists():
            ok = False
            item["error_code"] = "E_TASK_FILE_MISSING"
            details.append(item)
            continue
        text = p.read_text(encoding="utf-8")
        first = text.splitlines()[0] if text.splitlines() else ""
        expect = f"TASK_VERSION: {TASK_VERSION}"
        item["version_ok"] = first.strip() == expect
        if not item["version_ok"]:
            ok = False
            item["error_code"] = "E_TASK_VERSION_MISMATCH"
            item["expected"] = expect
            item["actual"] = first.strip()
        details.append(item)
    return {
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "error_code": "" if ok else "E_TASK_VERIFY_FAILED",
        "task_version": TASK_VERSION,
        "details": details,
    }


def remove_agent_native(cwd: Path) -> dict:
    project_dir = _project_agent_dir(cwd)
    if not project_dir.exists():
        return {"ok": True, "status": "PASS", "removed_or_archived": "", "global_policy_removed": "NO"}
    dst = project_dir.parent / f"agent_native.removed.{_now()}"
    project_dir.rename(dst)
    return {"ok": True, "status": "PASS", "removed_or_archived": str(dst), "global_policy_removed": "NO"}


def read_permission() -> dict:
    policy_path = _global_policy_path()
    legacy_path = _legacy_policy_path()
    chosen = policy_path if policy_path.exists() else legacy_path
    if not chosen.exists():
        return {
            "ok": False,
            "status": "MISSING",
            "policy_path": str(policy_path),
            "legacy_policy_path": str(legacy_path),
        }
    try:
        payload = json.loads(chosen.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "ok": False,
            "status": "FAIL",
            "policy_path": str(chosen),
            "canonical_policy_path": str(policy_path),
            "legacy_policy_path": str(legacy_path),
            "reason": str(exc),
        }
    return {
        "ok": True,
        "status": "PASS",
        "policy_path": str(chosen),
        "canonical_policy_path": str(policy_path),
        "legacy_policy_path": str(legacy_path),
        "policy": payload,
    }


def verify_permission_schema() -> dict:
    perm = read_permission()
    if not bool(perm.get("ok", False)):
        return {
            "ok": False,
            "status": str(perm.get("status", "MISSING")),
            "error_code": "E_POLICY_MISSING",
            "policy_path": perm.get("policy_path", ""),
        }
    payload = perm.get("policy", {})
    if not isinstance(payload, dict):
        return {"ok": False, "status": "FAIL", "error_code": "E_POLICY_NOT_OBJECT"}
    required = [
        "policy_version",
        "ms8_version",
        "agent_id",
        "permission_profile",
        "agent_mode",
        "execution_boundary",
        "created_at",
        "deny_shadow_system_access",
    ]
    missing = [k for k in required if k not in payload]
    invalid_profile = str(payload.get("permission_profile", "")).upper() not in PROFILES
    deny_shadow = payload.get("deny_shadow_system_access", None) is True
    ok = (not missing) and (not invalid_profile) and deny_shadow
    return {
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "error_code": "" if ok else "E_POLICY_SCHEMA_INVALID",
        "missing": missing,
        "invalid_profile": invalid_profile,
        "deny_shadow_system_access": payload.get("deny_shadow_system_access", None),
        "policy_path": perm.get("policy_path", ""),
    }
