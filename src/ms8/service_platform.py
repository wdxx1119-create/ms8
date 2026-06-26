"""Platform backends for MS8 background service management."""

from __future__ import annotations

import locale
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import Protocol

from .runtime import get_runtime_dir

LABEL = "com.ms8.watch"
ABSORB_LABEL = "com.ms8.absorb.watch"
OPENCLAW_LABELS = ("com.openclaw.memory.mcp", "com.openclaw.memory.maintenance")


def sanitize_service_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return cleaned or "project"


def project_memory_label(name: str) -> str:
    return f"com.ms8.project-memory.{sanitize_service_name(name)}.watch"


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


def program_arguments(*args: str) -> list[str]:
    return [sys.executable, "-m", "ms8", *args]


def _quoted_command(*args: str) -> str:
    return subprocess.list2cmdline(program_arguments(*args))


def _classify_windows_scheduler_error(stderr: str = "", stdout: str = "") -> dict[str, object]:
    combined = "\n".join([str(stderr or ""), str(stdout or "")]).strip()
    folded = combined.casefold()
    if "拒绝访问" in combined or "access is denied" in folded:
        return {
            "error_kind": "permission_denied",
            "permission_required": True,
            "scheduler_available": True,
        }
    if "schtasks_not_found" in folded:
        return {
            "error_kind": "scheduler_unavailable",
            "permission_required": False,
            "scheduler_available": False,
        }
    if "schtasks_timeout" in folded:
        return {
            "error_kind": "scheduler_timeout",
            "permission_required": False,
            "scheduler_available": True,
        }
    if "cannot find the file specified" in folded or "找不到指定的文件" in combined:
        return {
            "error_kind": "task_not_found",
            "permission_required": False,
            "scheduler_available": True,
        }
    return {
        "error_kind": "unknown",
        "permission_required": False,
        "scheduler_available": True,
    }


class ServiceBackend(Protocol):
    backend_name: str

    def install_watch(self, interval_seconds: int = 1800) -> dict: ...
    def remove_watch(self) -> dict: ...
    def watch_status(self) -> dict: ...
    def install_absorb(self) -> dict: ...
    def remove_absorb(self) -> dict: ...
    def absorb_status(self) -> dict: ...
    def install_project_memory(
        self,
        name: str,
        *,
        auto_build: bool = True,
        submit_summary: bool = True,
        auto_index: bool = True,
    ) -> dict: ...
    def remove_project_memory(self, name: str) -> dict: ...
    def project_memory_status(self, name: str) -> dict: ...


@dataclass
class GenericServiceBackend:
    backend_name: str
    reason: str

    def _unsupported(self, scope: str, **extra: object) -> dict:
        payload = {
            "ok": False,
            "supported": False,
            "backend": self.backend_name,
            "reason": self.reason,
            "scope": scope,
        }
        payload.update(extra)
        return payload

    def install_watch(self, interval_seconds: int = 1800) -> dict:
        return self._unsupported("watch", interval_seconds=interval_seconds)

    def remove_watch(self) -> dict:
        return self._unsupported("watch")

    def watch_status(self) -> dict:
        return {
            "ok": True,
            "supported": False,
            "backend": self.backend_name,
            "reason": self.reason,
            "installed": False,
            "running": False,
            "plist": "",
            "absorb_installed": False,
            "absorb_running": False,
            "absorb_plist": "",
            "openclaw_services": {label: False for label in OPENCLAW_LABELS},
        }

    def install_absorb(self) -> dict:
        return self._unsupported("absorb")

    def remove_absorb(self) -> dict:
        return self._unsupported("absorb")

    def absorb_status(self) -> dict:
        return {
            "ok": True,
            "supported": False,
            "backend": self.backend_name,
            "reason": self.reason,
            "installed": False,
            "running": False,
            "plist": "",
        }

    def install_project_memory(
        self,
        name: str,
        *,
        auto_build: bool = True,
        submit_summary: bool = True,
        auto_index: bool = True,
    ) -> dict:
        return self._unsupported(
            "project_memory",
            project=name,
            auto_build=auto_build,
            submit_summary=submit_summary,
            auto_index=auto_index,
            label=project_memory_label(name),
        )

    def remove_project_memory(self, name: str) -> dict:
        return self._unsupported("project_memory", project=name, label=project_memory_label(name))

    def project_memory_status(self, name: str) -> dict:
        return {
            "ok": True,
            "supported": False,
            "backend": self.backend_name,
            "reason": self.reason,
            "project": name,
            "label": project_memory_label(name),
            "installed": False,
            "running": False,
            "plist": "",
        }


@dataclass
class DarwinServiceBackend:
    backend_name: str = "launchd"

    def plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def absorb_plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{ABSORB_LABEL}.plist"

    def project_memory_plist_path(self, name: str) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{project_memory_label(name)}.plist"

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["launchctl", *args], capture_output=True, text=True)

    def _write_plist(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            plistlib.dump(payload, f)

    def install_watch(self, interval_seconds: int = 1800) -> dict:
        plist_path = self.plist_path()
        runtime = get_runtime_dir()
        logs = runtime / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": LABEL,
            "ProgramArguments": program_arguments("watch", "--interval", str(interval_seconds)),
            "RunAtLoad": True,
            "KeepAlive": True,
            "EnvironmentVariables": _common_env(runtime),
            "StandardOutPath": str(logs / "service.out.log"),
            "StandardErrorPath": str(logs / "service.err.log"),
        }
        self._write_plist(plist_path, payload)
        self._launchctl("unload", str(plist_path))
        load = self._launchctl("load", str(plist_path))
        return {
            "ok": load.returncode == 0,
            "backend": self.backend_name,
            "plist": str(plist_path),
            "stderr": load.stderr.strip(),
        }

    def remove_watch(self) -> dict:
        plist_path = self.plist_path()
        if plist_path.exists():
            self._launchctl("unload", str(plist_path))
            plist_path.unlink()
        return {"ok": True, "backend": self.backend_name, "plist": str(plist_path)}

    def install_absorb(self) -> dict:
        plist_path = self.absorb_plist_path()
        runtime = get_runtime_dir()
        logs = runtime / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": ABSORB_LABEL,
            "ProgramArguments": program_arguments("absorb", "start"),
            "RunAtLoad": True,
            "KeepAlive": True,
            "EnvironmentVariables": _common_env(runtime),
            "StandardOutPath": str(logs / "absorb-service.out.log"),
            "StandardErrorPath": str(logs / "absorb-service.err.log"),
        }
        self._write_plist(plist_path, payload)
        self._launchctl("unload", str(plist_path))
        load = self._launchctl("load", str(plist_path))
        return {
            "ok": load.returncode == 0,
            "backend": self.backend_name,
            "plist": str(plist_path),
            "stderr": load.stderr.strip(),
        }

    def remove_absorb(self) -> dict:
        plist_path = self.absorb_plist_path()
        if plist_path.exists():
            self._launchctl("unload", str(plist_path))
            plist_path.unlink()
        return {"ok": True, "backend": self.backend_name, "plist": str(plist_path)}

    def watch_status(self) -> dict:
        plist_path = self.plist_path()
        listed = self._launchctl("list")
        return {
            "ok": True,
            "supported": True,
            "backend": self.backend_name,
            "installed": plist_path.exists(),
            "running": LABEL in listed.stdout,
            "plist": str(plist_path),
            "absorb_installed": self.absorb_plist_path().exists(),
            "absorb_running": ABSORB_LABEL in listed.stdout,
            "absorb_plist": str(self.absorb_plist_path()),
            "openclaw_services": {label: label in listed.stdout for label in OPENCLAW_LABELS},
        }

    def absorb_status(self) -> dict:
        listed = self._launchctl("list")
        plist_path = self.absorb_plist_path()
        return {
            "ok": True,
            "supported": True,
            "backend": self.backend_name,
            "installed": plist_path.exists(),
            "running": ABSORB_LABEL in listed.stdout,
            "plist": str(plist_path),
        }

    def install_project_memory(
        self,
        name: str,
        *,
        auto_build: bool = True,
        submit_summary: bool = True,
        auto_index: bool = True,
    ) -> dict:
        label = project_memory_label(name)
        plist_path = self.project_memory_plist_path(name)
        runtime = get_runtime_dir()
        logs = runtime / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        args = ["absorb", "project-memory", "watch", "--name", name]
        if auto_build:
            args.append("--build")
        if submit_summary:
            args.append("--submit-summary")
        if not auto_index:
            args.append("--no-index")
        payload = {
            "Label": label,
            "ProgramArguments": program_arguments(*args),
            "RunAtLoad": True,
            "KeepAlive": True,
            "EnvironmentVariables": _common_env(runtime),
            "StandardOutPath": str(logs / f"project-memory-{sanitize_service_name(name)}.out.log"),
            "StandardErrorPath": str(logs / f"project-memory-{sanitize_service_name(name)}.err.log"),
        }
        self._write_plist(plist_path, payload)
        self._launchctl("unload", str(plist_path))
        load = self._launchctl("load", str(plist_path))
        return {
            "ok": load.returncode == 0,
            "backend": self.backend_name,
            "project": name,
            "label": label,
            "plist": str(plist_path),
            "stderr": load.stderr.strip(),
        }

    def remove_project_memory(self, name: str) -> dict:
        plist_path = self.project_memory_plist_path(name)
        label = project_memory_label(name)
        if plist_path.exists():
            self._launchctl("unload", str(plist_path))
            plist_path.unlink()
        return {
            "ok": True,
            "backend": self.backend_name,
            "project": name,
            "label": label,
            "plist": str(plist_path),
        }

    def project_memory_status(self, name: str) -> dict:
        label = project_memory_label(name)
        plist_path = self.project_memory_plist_path(name)
        listed = self._launchctl("list")
        return {
            "ok": True,
            "supported": True,
            "backend": self.backend_name,
            "project": name,
            "label": label,
            "installed": plist_path.exists(),
            "running": label in listed.stdout,
            "plist": str(plist_path),
        }


@dataclass
class WindowsTaskSchedulerBackend:
    backend_name: str = "schtasks"

    def _task_name(self, label: str) -> str:
        return f"MS8-{label}"

    def _run_schtasks(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["schtasks", *args],
                capture_output=True,
                text=True,
                encoding=locale.getpreferredencoding(False) or "utf-8",
                errors="replace",
                timeout=20,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                ["schtasks", *args],
                returncode=127,
                stdout="",
                stderr="schtasks_not_found",
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                ["schtasks", *args],
                returncode=124,
                stdout="",
                stderr="schtasks_timeout",
            )

    def _create_task(self, label: str, command: str) -> dict:
        task_name = self._task_name(label)
        create = self._run_schtasks(
            "/Create",
            "/SC",
            "ONLOGON",
            "/TN",
            task_name,
            "/TR",
            command,
            "/F",
        )
        if create.returncode != 0:
            payload = {
                "ok": False,
                "backend": self.backend_name,
                "label": label,
                "task_name": task_name,
                "stderr": create.stderr.strip(),
                "stdout": create.stdout.strip(),
            }
            payload.update(_classify_windows_scheduler_error(create.stderr, create.stdout))
            return payload
        run_now = self._run_schtasks("/Run", "/TN", task_name)
        return {
            "ok": True,
            "backend": self.backend_name,
            "label": label,
            "task_name": task_name,
            "stderr": create.stderr.strip(),
            "stdout": create.stdout.strip(),
            "run_now_ok": run_now.returncode == 0,
            "run_now_stderr": run_now.stderr.strip(),
        }

    def _delete_task(self, label: str) -> dict:
        task_name = self._task_name(label)
        delete = self._run_schtasks("/Delete", "/TN", task_name, "/F")
        payload = {
            "ok": delete.returncode == 0,
            "backend": self.backend_name,
            "label": label,
            "task_name": task_name,
            "stderr": delete.stderr.strip(),
            "stdout": delete.stdout.strip(),
        }
        if delete.returncode != 0:
            payload.update(_classify_windows_scheduler_error(delete.stderr, delete.stdout))
        return payload

    def _query_task(self, label: str) -> dict:
        task_name = self._task_name(label)
        query = self._run_schtasks("/Query", "/TN", task_name, "/FO", "LIST", "/V")
        exists = query.returncode == 0
        stdout = query.stdout or ""
        running_markers = ("Running", "正在运行")
        ready_markers = ("Ready", "已准备")
        running = any(marker in stdout for marker in running_markers)
        registered = exists and (running or any(marker in stdout for marker in ready_markers) or bool(stdout.strip()))
        return {
            "ok": True,
            "supported": True,
            "backend": self.backend_name,
            "label": label,
            "task_name": task_name,
            "installed": exists,
            "running": running,
            "plist": "",
            "scheduler_state": "running" if running else ("registered" if registered else "missing"),
            "raw": stdout.strip(),
        }

    def install_watch(self, interval_seconds: int = 1800) -> dict:
        payload = self._create_task(LABEL, _quoted_command("watch", "--interval", str(interval_seconds)))
        payload["interval_seconds"] = interval_seconds
        return payload

    def remove_watch(self) -> dict:
        return self._delete_task(LABEL)

    def watch_status(self) -> dict:
        payload = self._query_task(LABEL)
        absorb = self._query_task(ABSORB_LABEL)
        payload["absorb_installed"] = absorb.get("installed", False)
        payload["absorb_running"] = absorb.get("running", False)
        payload["absorb_plist"] = ""
        payload["openclaw_services"] = {label: False for label in OPENCLAW_LABELS}
        return payload

    def install_absorb(self) -> dict:
        return self._create_task(ABSORB_LABEL, _quoted_command("absorb", "start"))

    def remove_absorb(self) -> dict:
        return self._delete_task(ABSORB_LABEL)

    def absorb_status(self) -> dict:
        return self._query_task(ABSORB_LABEL)

    def install_project_memory(
        self,
        name: str,
        *,
        auto_build: bool = True,
        submit_summary: bool = True,
        auto_index: bool = True,
    ) -> dict:
        label = project_memory_label(name)
        args = ["absorb", "project-memory", "watch", "--name", name]
        if auto_build:
            args.append("--build")
        if submit_summary:
            args.append("--submit-summary")
        if not auto_index:
            args.append("--no-index")
        payload = self._create_task(label, _quoted_command(*args))
        payload["project"] = name
        payload["auto_build"] = auto_build
        payload["submit_summary"] = submit_summary
        payload["auto_index"] = auto_index
        return payload

    def remove_project_memory(self, name: str) -> dict:
        payload = self._delete_task(project_memory_label(name))
        payload["project"] = name
        return payload

    def project_memory_status(self, name: str) -> dict:
        payload = self._query_task(project_memory_label(name))
        payload["project"] = name
        return payload


def current_service_backend() -> ServiceBackend:
    if sys.platform == "darwin":
        return DarwinServiceBackend()
    if sys.platform == "win32":
        return WindowsTaskSchedulerBackend()
    return GenericServiceBackend(backend_name=sys.platform, reason="platform_service_backend_not_implemented")
