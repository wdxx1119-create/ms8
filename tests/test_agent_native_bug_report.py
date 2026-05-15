from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from ms8.agent_native.agent_cli import run_agent_cli


def test_agent_bug_report_bundle_created(tmp_path: Path, monkeypatch) -> None:
    runtime_home = tmp_path / "runtime"
    monkeypatch.setenv("MS8_HOME", str(runtime_home))
    monkeypatch.chdir(tmp_path)

    args = Namespace(agent_cmd="bug-report", bundle=True, redact=True)
    code = run_agent_cli(args)
    assert code == 0

    report_root = runtime_home / "bug_reports"
    bundles = sorted(report_root.glob("*"))
    assert bundles, "expected at least one bug-report bundle"
    latest = bundles[-1]
    assert (latest / "redaction_note.txt").exists()
    assert (latest / "doctor_output.txt").exists()
    assert (latest / "ms8_status.txt").exists()
    assert (latest / "agent_native_status.json").exists()

