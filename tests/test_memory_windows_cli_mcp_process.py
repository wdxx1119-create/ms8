from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires a real Windows process environment")


def _venv_executable(name: str) -> Path:
    suffix = ".exe"
    candidate = Path(sys.executable).resolve().parent / f"{name}{suffix}"
    assert candidate.is_file(), f"installed entry point is missing: {candidate}"
    return candidate


def _isolated_environment(tmp_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["MS8_HOME"] = str(tmp_path / "Windows 用户" / "MS8 isolated home")
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def test_installed_ms8_entry_points_run_from_powershell(tmp_path: Path) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell is not None, "PowerShell is required for Windows CLI acceptance"
    ms8 = _venv_executable("ms8")
    ledger = _venv_executable("ms8-memory-ledger")
    environment = _isolated_environment(tmp_path)

    version = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", f"& '{ms8}' version"],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert version.returncode == 0, version.stdout + version.stderr
    assert "ms8" in version.stdout.casefold()

    help_result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", f"& '{ledger}' --help"],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    assert "memory-ledger" in help_result.stdout.casefold()


def test_windows_mcp_stdio_process_handles_newline_json_and_exits_cleanly(tmp_path: Path) -> None:
    environment = _isolated_environment(tmp_path)
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "windows-acceptance"}},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in requests)
    completed = subprocess.run(
        [sys.executable, "-m", "ms8.connect.mcp_server.stdio_server"],
        input=payload,
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert [item["id"] for item in responses] == [1, 2]
    assert responses[0]["result"]["serverInfo"]["name"] == "ms8-memory"
    tool_names = {item["name"] for item in responses[1]["result"]["tools"]}
    assert {"query", "context", "prepare_reply", "pre_action_check"}.issubset(tool_names)


def test_windows_mcp_stdio_process_handles_content_length_frame(tmp_path: Path) -> None:
    environment = _isolated_environment(tmp_path)
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    }
    body = json.dumps(request, ensure_ascii=False).encode("utf-8")
    framed = b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
    completed = subprocess.run(
        [sys.executable, "-m", "ms8.connect.mcp_server.stdio_server"],
        input=framed,
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    header, response_body = completed.stdout.split(b"\r\n\r\n", 1)
    assert header.lower().startswith(b"content-length:")
    response = json.loads(response_body.decode("utf-8"))
    assert response["id"] == 7
    assert response["result"]["protocolVersion"] == "2025-11-25"
