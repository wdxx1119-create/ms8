"""CLI handlers for agent-native phase-1 commands."""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from .onboarding import (
    init_agent_native,
    list_tasks,
    migrate_policy_path,
    read_permission,
    remove_agent_native,
    show_task,
    verify_permission_schema,
    verify_tasks,
)
from .report import block


def _bundle_dir() -> Path:
    from ..runtime import get_runtime_dir

    return get_runtime_dir() / "bug_reports"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_bug_report_bundle(*, redact: bool = True) -> dict:
    from ..doctor import run_doctor
    from ..runtime import engine_status, export_support_bundle_runtime, get_runtime_dir

    bundle_root = _bundle_dir() / _utc_stamp()
    bundle_root.mkdir(parents=True, exist_ok=True)

    # Minimal phase-1 files.
    _write_text(bundle_root / "ms8_status.txt", json.dumps(engine_status(), ensure_ascii=False, indent=2))

    doctor_file = bundle_root / "doctor_output.txt"
    try:
        # Keep behavior simple: execute doctor and only store exit code summary.
        code = run_doctor()
        _write_text(doctor_file, f"doctor_exit_code={code}\n")
    except (OSError, RuntimeError) as exc:  # pragma: no cover
        _write_text(doctor_file, f"doctor_error={exc}\n")

    perm = read_permission()
    _write_text(
        bundle_root / "agent_native_status.json",
        json.dumps(
            {
                "status": perm.get("status", "MISSING"),
                "policy_path": perm.get("policy_path", ""),
                "permission_profile": (
                    perm.get("policy", {}).get("permission_profile", "N/A")
                    if isinstance(perm.get("policy"), dict)
                    else "N/A"
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    _write_text(
        bundle_root / "redaction_note.txt",
        (
            "This report is redacted.\n"
            "No memory content, secrets, passwords, raw private documents, or shadow "
            "system internals are included.\n"
            "Upload is not performed automatically.\n"
        ),
    )
    _write_text(
        bundle_root / "system_info.json",
        json.dumps(
            {
                "runtime_dir": str(get_runtime_dir()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "redact": bool(redact),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    # Also produce existing support bundle zip for maintainers.
    support = export_support_bundle_runtime(
        output=str(bundle_root / "support_bundle.zip"),
        redact=bool(redact),
        dry_run=False,
    )
    return {
        "ok": True,
        "status": "CREATED",
        "bundle_path": str(bundle_root),
        "included": [
            "system_info.json",
            "ms8_status.txt",
            "doctor_output.txt",
            "agent_native_status.json",
            "redaction_note.txt",
            "support_bundle.zip",
        ],
        "excluded_for_privacy": [
            "memory content",
            "api keys",
            "passwords",
            "tokens",
            "shadow internals",
        ],
        "upload_performed": "NO",
        "support_bundle": support,
    }


def _run_check_flow(*, allow_repair_preview: bool = True) -> dict:
    from ..doctor import run_doctor
    from ..runtime import engine_status, self_repair_run_runtime

    buf = StringIO()
    doctor_code = 1
    try:
        with redirect_stdout(buf):
            doctor_code = run_doctor()
    except (OSError, RuntimeError) as exc:  # pragma: no cover
        return {
            "ok": False,
            "status": "FAIL",
            "error_code": "E_DOCTOR_RUN_FAILED",
            "reason": str(exc),
        }
    doctor_text = buf.getvalue()
    status_obj = engine_status()
    perm = read_permission()
    policy = perm.get("policy", {}) if isinstance(perm.get("policy"), dict) else {}
    profile = str(policy.get("permission_profile", "N/A"))

    issue_found = (
        ("Overall: degraded" in doctor_text)
        or ("Overall: FAIL" in doctor_text)
        or ("⚠️" in doctor_text)
        or ("❌" in doctor_text)
    )
    repair_plan = "No repair needed."
    repair_preview = None
    if issue_found:
        if profile == "TRUSTED_AGENT" and allow_repair_preview:
            repair_preview = self_repair_run_runtime(mode="dry-run")
            if bool(repair_preview.get("ok", False)):
                repair_plan = "Dry-run repair preview generated."
            else:
                repair_plan = "Dry-run repair preview failed."
        else:
            repair_plan = "Upgrade to TRUSTED_AGENT to see repair preview."
    return {
        "ok": True,
        "status": "PASS",
        "error_code": "",
        "doctor_exit_code": doctor_code,
        "doctor_overall": "degraded" if doctor_code != 0 else "healthy",
        "engine_available": bool(status_obj.get("available", False)),
        "permission_profile": profile,
        "issue_found": issue_found,
        "repair_plan": repair_plan,
        "repair_preview": repair_preview if isinstance(repair_preview, dict) else {},
    }


def _run_report_flow(*, redact: bool = True) -> dict:
    from ..doctor import run_doctor

    buf = StringIO()
    code = 1
    with redirect_stdout(buf):
        code = run_doctor()
    doctor_text = buf.getvalue()
    critical_issue = ("Overall: degraded" in doctor_text) or ("Overall: FAIL" in doctor_text)
    bundle = None
    if critical_issue:
        bundle = _build_bug_report_bundle(redact=bool(redact))
    return {
        "ok": True,
        "status": "PASS",
        "error_code": "",
        "doctor_exit_code": code,
        "critical_issue": critical_issue,
        "bug_bundle": bundle if isinstance(bundle, dict) else {"status": "not_required"},
    }


def _run_install_flow(*, profile: str = "DEFAULT_SAFE", confirm: bool = False) -> dict:
    from ..doctor import run_doctor
    from ..runtime import engine_status

    chosen = str(profile or "DEFAULT_SAFE").strip().upper()
    if chosen not in {"DEFAULT_SAFE", "TRUSTED_AGENT"}:
        chosen = "DEFAULT_SAFE"
    # Default shape required by MS8_FIRST_INSTALL_REPORT.
    report: dict = {
        "ok": False,
        "status": "FAIL",
        "error_code": "E_INSTALL_FLOW_UNKNOWN",
        "permission_profile": "",
        "python_version": "UNKNOWN",
        "pip_status": "UNKNOWN",
        "ms8_already_installed": "UNKNOWN",
        "install_status": "NOT_RUN",
        "agent_init_status": "NOT_RUN",
        "doctor_status": "NOT_RUN",
        "ms8_status": "NOT_RUN",
        "learned_tasks": "NOT_RUN",
        "safety_notes": "",
        "user_guide": "Run: python -m ms8 agent task show usage",
        "next_action": "Run: python -m ms8 agent task show usage",
    }
    report["permission_profile"] = chosen

    def _run(cmd: list[str]) -> tuple[int, str]:
        import subprocess
        import sys

        normalized = list(cmd)
        if normalized and normalized[0] == "python":
            normalized[0] = sys.executable

        p = subprocess.run(normalized, capture_output=True, text=True, check=False)
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return int(p.returncode), out.strip()

    py_code, py_out = _run(["python", "--version"])
    report["python_version"] = py_out.splitlines()[0] if py_code == 0 and py_out else "UNKNOWN"
    pip_code, _ = _run(["python", "-m", "pip", "--version"])
    report["pip_status"] = "PASS" if pip_code == 0 else "FAIL"

    if chosen == "TRUSTED_AGENT" and not confirm:
        report.update(
            {
                "status": "NEEDS_CONFIRM",
                "error_code": "E_NEEDS_CONFIRM",
                "install_status": "NOT_RUN",
                "safety_notes": "Waiting for explicit TRUSTED_AGENT confirmation.",
                "next_action": "Re-run with --confirm when using TRUSTED_AGENT.",
            }
        )
        return report

    version_code = 0
    try:
        version_code, _ = _run(["python", "-m", "ms8", "version"])
    except (FileNotFoundError, OSError):
        version_code = 1
    already_installed = version_code == 0
    report["ms8_already_installed"] = bool(already_installed)
    report["install_status"] = "SKIPPED" if already_installed else "PASS"

    init_out = init_agent_native(
        profile=chosen,
        cwd=Path.cwd(),
        force=True,
        dry_run=False,
        confirm=bool(confirm),
    )
    report["agent_init_status"] = str(init_out.get("status", "FAIL")).upper()
    if str(init_out.get("status", "")).upper() == "NEEDS_CONFIRM":
        report.update(
            {
                "status": "NEEDS_CONFIRM",
                "error_code": "E_NEEDS_CONFIRM",
                "doctor_status": "NOT_RUN",
                "ms8_status": "NOT_RUN",
                "learned_tasks": "NOT_RUN",
                "next_action": "Confirm TRUSTED_AGENT explicitly.",
            }
        )
        return report

    doctor_buf = StringIO()
    doctor_code = 1
    with redirect_stdout(doctor_buf):
        doctor_code = run_doctor()
    doctor_text = doctor_buf.getvalue()
    if "Overall: healthy" in doctor_text:
        report["doctor_status"] = "PASS"
    elif "Overall: degraded" in doctor_text:
        report["doctor_status"] = "WARN"
    else:
        report["doctor_status"] = "FAIL" if doctor_code != 0 else "PASS"
    eng = engine_status()
    report["ms8_status"] = "PASS" if bool(eng.get("available", False)) else "FAIL"
    report["learned_tasks"] = "usage,ops,check,report"
    report["ok"] = bool(report["agent_init_status"] in {"PASS", "ALREADY_INSTALLED"}) and report["ms8_status"] == "PASS"
    report["error_code"] = "" if report["ok"] else "E_INSTALL_FLOW_PARTIAL"
    report["status"] = "ALREADY_INSTALLED" if already_installed else ("PASS" if report["ok"] else "FAIL")
    report["safety_notes"] = "Phase-1: real repair disabled; no shadow system access."
    return report


def run_agent_cli(args) -> int:
    def _emit(title: str, payload: dict, ok: bool) -> int:
        base = {
            "report_version": 1,
            "status": payload.get("status", "PASS" if ok else "FAIL"),
            "error_code": payload.get("error_code", "" if ok else "E_UNKNOWN"),
            "next_action": payload.get("next_action", ""),
        }
        merged = {**base, **payload}
        print(block(title, merged))
        return 0 if ok else 1

    cmd = str(getattr(args, "agent_cmd", "") or "")
    cwd = Path.cwd()

    if cmd == "init":
        out = init_agent_native(
            profile=str(getattr(args, "profile", "DEFAULT_SAFE")),
            cwd=cwd,
            force=bool(getattr(args, "force", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            confirm=bool(getattr(args, "confirm", False)),
        )
        out["next_action"] = "Run: python -m ms8 agent task show usage"
        return _emit("MS8_AGENT_INIT_RESULT", out, str(out.get("status", "")).upper() == "PASS")

    if cmd == "permission":
        sub = str(getattr(args, "permission_cmd", "") or "")
        if sub == "upgrade":
            target = str(getattr(args, "to", "TRUSTED_AGENT") or "TRUSTED_AGENT")
            out = init_agent_native(
                profile=target,
                cwd=cwd,
                force=True,
                dry_run=bool(getattr(args, "dry_run", False)),
                confirm=bool(getattr(args, "confirm", False)),
            )
            return _emit("MS8_AGENT_PERMISSION", out, str(out.get("status", "")).upper() == "PASS")
        perm = read_permission()
        policy = perm.get("policy", {}) if isinstance(perm.get("policy"), dict) else {}
        out = {
            "report_version": 1,
            "status": perm.get("status", "MISSING"),
            "permission_profile": policy.get("permission_profile", "N/A"),
            "agent_mode": policy.get("agent_mode", "N/A"),
            "execution_boundary": policy.get("execution_boundary", "N/A"),
            "policy_path": perm.get("policy_path", ""),
            "allowed": [k for k, v in policy.items() if str(k).startswith("allow_") and bool(v)],
            "denied": [k for k, v in policy.items() if str(k).startswith("deny_") and bool(v)],
            "next_action": "Run: python -m ms8 agent permission upgrade --to TRUSTED_AGENT --confirm",
        }
        return _emit("MS8_AGENT_PERMISSION", out, str(out.get("status", "")).upper() == "PASS")

    if cmd == "policy":
        sub = str(getattr(args, "policy_cmd", "") or "")
        if sub == "verify":
            out = verify_permission_schema()
            out["next_action"] = "Run: python -m ms8 agent init --profile DEFAULT_SAFE --force"
            return _emit("MS8_AGENT_POLICY_VERIFY", out, bool(out.get("ok", False)))
        print("ms8 agent policy: choose verify")
        return 2

    if cmd == "run":
        sub = str(getattr(args, "run_cmd", "") or "")
        if sub == "install":
            out = _run_install_flow(
                profile=str(getattr(args, "profile", "DEFAULT_SAFE")),
                confirm=bool(getattr(args, "confirm", False)),
            )
            return _emit("MS8_FIRST_INSTALL_REPORT", out, bool(out.get("ok", False)))
        if sub == "check":
            out = _run_check_flow(allow_repair_preview=not bool(getattr(args, "no_repair_preview", False)))
            out["next_action"] = "Run: python -m ms8 agent run report"
            return _emit("MS8_AGENT_RUN_CHECK", out, bool(out.get("ok", False)))
        if sub == "report":
            out = _run_report_flow(redact=not bool(getattr(args, "no_redact", False)))
            out["next_action"] = "If critical_issue=true, share bug bundle path with maintainer."
            return _emit("MS8_AGENT_RUN_REPORT", out, bool(out.get("ok", False)))
        if sub == "daily":
            check = _run_check_flow(allow_repair_preview=not bool(getattr(args, "no_repair_preview", False)))
            report = _run_report_flow(redact=not bool(getattr(args, "no_redact", False)))
            ok = bool(check.get("ok", False)) and bool(report.get("ok", False))
            verbose_output = bool(getattr(args, "verbose_output", False))
            if verbose_output:
                check_out: dict = check
                report_out: dict = report
            else:
                check_out = {
                    "doctor_overall": check.get("doctor_overall", "unknown"),
                    "permission_profile": check.get("permission_profile", "N/A"),
                    "issue_found": bool(check.get("issue_found", False)),
                    "repair_plan": check.get("repair_plan", ""),
                }
                report_out = {
                    "critical_issue": bool(report.get("critical_issue", False)),
                    "bug_bundle_status": (
                        report.get("bug_bundle", {}).get("status", "not_required")
                        if isinstance(report.get("bug_bundle"), dict)
                        else "not_required"
                    ),
                }
            summary_8 = [
                f"1.status={'PASS' if ok else 'FAIL'}",
                f"2.doctor_overall={check.get('doctor_overall', 'unknown')}",
                f"3.permission_profile={check.get('permission_profile', 'N/A')}",
                f"4.issue_found={bool(check.get('issue_found', False))}",
                f"5.repair_plan={check.get('repair_plan', '')}",
                f"6.critical_issue={bool(report.get('critical_issue', False))}",
                (
                    "7.bug_bundle_status="
                    + (
                        report.get("bug_bundle", {}).get("status", "not_required")
                        if isinstance(report.get("bug_bundle"), dict)
                        else "not_required"
                    )
                ),
                "8.next_action=If critical_issue=true share bug bundle path with maintainer.",
            ]
            out = {
                "ok": ok,
                "status": "PASS" if ok else "FAIL",
                "error_code": "" if ok else "E_AGENT_DAILY_FAILED",
                "check": check_out,
                "report": report_out,
                "verbose_output": verbose_output,
                "summary_8": summary_8,
                "next_action": "If report.critical_issue=true, share bug bundle path with maintainer.",
            }
            return _emit("MS8_AGENT_RUN_DAILY", out, ok)
        print("ms8 agent run: choose install|check|report|daily")
        return 2

    if cmd == "task":
        sub = str(getattr(args, "task_cmd", "") or "")
        if sub == "list":
            out = list_tasks(cwd)
            return _emit("MS8_AGENT_TASK_LIST", out, bool(out.get("ok", False)))
        if sub == "verify":
            out = verify_tasks(cwd)
            out["next_action"] = "Run: python -m ms8 agent init --force"
            return _emit("MS8_AGENT_TASK_VERIFY", out, bool(out.get("ok", False)))
        if sub == "show":
            out = show_task(cwd, str(getattr(args, "name", "") or ""))
            meta = dict(out)
            meta.pop("content", None)
            _emit("MS8_AGENT_TASK_SHOW", meta, bool(out.get("ok", False)))
            if bool(out.get("ok", False)) and out.get("content"):
                print(out["content"])
            return 0 if bool(out.get("ok", False)) else 1
        print("ms8 agent task: choose list|verify|show")
        return 2

    if cmd == "remove":
        out = remove_agent_native(cwd)
        return _emit("MS8_AGENT_REMOVE_RESULT", out, bool(out.get("ok", False)))

    if cmd == "migrate-policy-path":
        out = migrate_policy_path(
            dry_run=bool(getattr(args, "dry_run", False)),
            force=bool(getattr(args, "force", False)),
            cleanup_legacy=bool(getattr(args, "cleanup_legacy", False)),
        )
        return _emit("MS8_AGENT_POLICY_MIGRATION", out, bool(out.get("ok", False)))

    if cmd == "bug-report":
        out = _build_bug_report_bundle(redact=bool(getattr(args, "redact", True)))
        out["next_action"] = "Share bundle path with maintainer"
        return _emit("MS8_BUG_REPORT", out, bool(out.get("ok", False)))

    print("ms8 agent: choose init|permission|policy|run|task|remove|migrate-policy-path|bug-report")
    return 2
