from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_example(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def test_isolated_cli_example_has_help_without_creating_runtime() -> None:
    result = _run_example("examples/isolated_cli.py", "--help")
    assert result.returncode == 0, result.stderr
    assert "isolated temporary runtime" in result.stdout


def test_parse_local_text_example_reports_metadata_without_content_by_default(tmp_path: Path) -> None:
    source = tmp_path / "authorized note.txt"
    source.write_text("synthetic example content\n", encoding="utf-8")

    result = _run_example("examples/parse_local_text.py", str(source))

    assert result.returncode == 0, result.stderr
    assert "parse_status: parsed" in result.stdout
    assert "content_hash:" in result.stdout
    assert "content_chars:" in result.stdout
    assert "submitted_to_ms8: false" in result.stdout
    assert "synthetic example content" not in result.stdout


def test_parse_local_text_example_requires_explicit_preview(tmp_path: Path) -> None:
    source = tmp_path / "preview.txt"
    source.write_text("synthetic preview content\n", encoding="utf-8")

    result = _run_example(
        "examples/parse_local_text.py",
        str(source),
        "--show-preview",
        "--preview-chars",
        "40",
    )

    assert result.returncode == 0, result.stderr
    assert "preview: synthetic preview content" in result.stdout
