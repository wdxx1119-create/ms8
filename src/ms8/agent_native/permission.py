"""Permission profiles for MS8 agent-native."""

from __future__ import annotations

from datetime import datetime, timezone

PROFILES = {"DEFAULT_SAFE", "TRUSTED_AGENT"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_policy(profile: str, ms8_version: str, agent_id: str = "default") -> dict:
    p = str(profile).strip().upper()
    if p not in PROFILES:
        raise ValueError(f"unsupported_profile:{profile}")
    base = {
        "policy_version": "1",
        "ms8_version": ms8_version or "unknown",
        "agent_id": agent_id,
        "permission_profile": p,
        "agent_mode": "ms8_builtin_tools_only",
        "execution_boundary": "command_runner_only",
        "created_at": _now(),
        "last_updated_at": _now(),
        "allow_install": True,
        "allow_agent_init": True,
        "allow_doctor": True,
        "allow_engine_status": True,
        "allow_summary_report": True,
        "allow_bug_report_bundle": True,
        "allow_safe_repair": False,
        "allow_safe_update": False,
        "allow_schedule": False,
        "deny_direct_file_edit": True,
        "deny_database_edit": True,
        "deny_memory_delete": True,
        "deny_memory_rewrite": True,
        "deny_security_policy_change": True,
        "deny_sudo": True,
        "deny_shell_rc_modify": True,
        "deny_shadow_system_access": True,
        "deny_upload_without_user_confirm": True,
    }
    if p == "DEFAULT_SAFE":
        base.update(
            {
                "allow_safe_repair_dry_run": False,
                "allow_backup_create": False,
                "allow_feature_enable": False,
                "allow_feature_disable": False,
            }
        )
    else:
        base.update(
            {
                "allow_safe_repair_dry_run": True,
                "allow_backup_create": True,
                "allow_feature_enable": True,
                "allow_feature_disable": True,
            }
        )
    return base

