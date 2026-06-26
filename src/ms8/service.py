"""Public service management entrypoints for MS8."""

from __future__ import annotations

from .absorb.project_memory.scope import list_projects
from .service_platform import (
    ABSORB_LABEL,
    LABEL,
    OPENCLAW_LABELS,
    current_service_backend,
    program_arguments,
    project_memory_label,
    sanitize_service_name,
)


_program_arguments = program_arguments


def _backend():
    return current_service_backend()


def _with_windows_service_hint(payload: dict, *, action: str, project: str | None = None) -> dict:
    if bool(payload.get("ok", False)) or payload.get("backend") != "schtasks":
        return payload
    error_kind = str(payload.get("error_kind", "unknown") or "unknown")
    action_hint = (
        f"ms8 absorb project-memory watch --name {project}" if project else "ms8 watch --once"
    )
    next_actions = [action_hint]
    if error_kind == "permission_denied":
        next_actions.append("Retry background service install from a terminal with sufficient Windows Task Scheduler permissions.")
    elif error_kind == "scheduler_unavailable":
        next_actions.append("Ensure Windows Task Scheduler (`schtasks`) is available in PATH, then retry.")
    elif error_kind == "scheduler_timeout":
        next_actions.append("Retry the background service install after Task Scheduler becomes responsive.")
    else:
        next_actions.append("Retry background service install after checking Windows Task Scheduler state and task naming.")
    reason_suffix = {
        "permission_denied": "permission_denied",
        "scheduler_unavailable": "scheduler_unavailable",
        "scheduler_timeout": "scheduler_timeout",
        "task_not_found": "task_not_found",
        "unknown": f"{action}_failed",
    }.get(error_kind, f"{action}_failed")
    hint = {
        "service_mode": "background_scheduler",
        "fallback_mode": "foreground_watch",
        "next_actions": next_actions,
        "reason_code": f"windows_service_{reason_suffix}",
    }
    merged = dict(payload)
    merged.update({k: v for k, v in hint.items() if k not in merged})
    return merged


def install_service(interval_seconds: int = 1800) -> dict:
    return _backend().install_watch(interval_seconds=interval_seconds)


def remove_service() -> dict:
    return _backend().remove_watch()


def install_absorb_service() -> dict:
    return _backend().install_absorb()


def remove_absorb_service() -> dict:
    return _backend().remove_absorb()


def service_status() -> dict:
    return _backend().watch_status()


def absorb_service_status() -> dict:
    return _backend().absorb_status()


def install_project_memory_service(
    name: str,
    *,
    auto_build: bool = True,
    submit_summary: bool = True,
    auto_index: bool = True,
) -> dict:
    payload = _backend().install_project_memory(
        name,
        auto_build=auto_build,
        submit_summary=submit_summary,
        auto_index=auto_index,
    )
    return _with_windows_service_hint(payload, action="install", project=name)


def remove_project_memory_service(name: str) -> dict:
    payload = _backend().remove_project_memory(name)
    if payload.get("backend") == "schtasks" and not bool(payload.get("ok", False)):
        status = _backend().project_memory_status(name)
        if not bool(status.get("installed", False)):
            normalized = dict(payload)
            normalized["ok"] = True
            normalized["removed"] = False
            normalized["reason_code"] = "windows_service_already_missing"
            normalized["next_actions"] = [f"ms8 absorb project-memory service-install --name {name}"]
            return normalized
        return _with_windows_service_hint(payload, action="remove", project=name)
    return payload


def project_memory_service_status(name: str) -> dict:
    return _backend().project_memory_status(name)


def install_all_project_memory_services(
    *,
    auto_build: bool = True,
    submit_summary: bool = True,
    auto_index: bool = True,
) -> dict:
    projects = list_projects()
    results = [
        install_project_memory_service(
            str(item.get("name", "")),
            auto_build=auto_build,
            submit_summary=submit_summary,
            auto_index=auto_index,
        )
        for item in projects
        if str(item.get("name", ""))
    ]
    installed = sum(1 for item in results if bool(item.get("ok", False)))
    failed = len(results) - installed
    ok = failed == 0
    return {
        "ok": ok,
        "registered_projects": len(projects),
        "services_installed": installed,
        "services_failed": failed,
        "results": results,
    }


def remove_all_project_memory_services() -> dict:
    projects = list_projects()
    results = [
        remove_project_memory_service(str(item.get("name", "")))
        for item in projects
        if str(item.get("name", ""))
    ]
    return {
        "ok": all(bool(item.get("ok", False)) for item in results) if results else True,
        "registered_projects": len(projects),
        "services_removed": len(results),
        "results": results,
    }


def project_memory_services_status_all() -> dict:
    from .absorb.project_memory.health import _runtime_mode, _watch_support

    projects = list_projects()
    watch_support = _watch_support()
    results = []
    runtime_mode_counts: dict[str, int] = {}
    for item in projects:
        name = str(item.get("name", ""))
        if not name:
            continue
        service_state = project_memory_service_status(name)
        runtime_mode = _runtime_mode(service_state, watch_support)
        combined = dict(service_state)
        combined.update(runtime_mode)
        results.append(combined)
        mode_key = str(runtime_mode.get("recommended_runtime_mode", "") or "")
        if mode_key:
            runtime_mode_counts[mode_key] = runtime_mode_counts.get(mode_key, 0) + 1
    installed = sum(1 for item in results if bool(item.get("installed", False)))
    running = sum(1 for item in results if bool(item.get("running", False)))
    background_ready = sum(1 for item in results if bool(item.get("background_service_ready", False)))
    foreground_ready = sum(1 for item in results if bool(item.get("foreground_watch_available", False)))
    return {
        "ok": True,
        "registered_projects": len(projects),
        "installed_services": installed,
        "running_services": running,
        "background_service_ready_projects": background_ready,
        "foreground_watch_available_projects": foreground_ready,
        "recommended_runtime_modes": dict(sorted(runtime_mode_counts.items())),
        "results": results,
    }


__all__ = [
    "LABEL",
    "ABSORB_LABEL",
    "OPENCLAW_LABELS",
    "sanitize_service_name",
    "project_memory_label",
    "program_arguments",
    "install_service",
    "remove_service",
    "install_absorb_service",
    "remove_absorb_service",
    "service_status",
    "absorb_service_status",
    "install_project_memory_service",
    "remove_project_memory_service",
    "project_memory_service_status",
    "install_all_project_memory_services",
    "remove_all_project_memory_services",
    "project_memory_services_status_all",
]
