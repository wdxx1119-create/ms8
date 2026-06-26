from __future__ import annotations

import os
from pathlib import Path

import pytest

from ms8.absorb.project_memory.scanner import scan_project


def test_scan_rejects_missing_project_root_without_mutating_index(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    db_path = tmp_path / "state" / "project.sqlite"
    index_state_path = tmp_path / "state" / "index_state.json"

    result = scan_project(
        project_name="missing",
        project_root=root,
        db_path=db_path,
        index_state_path=index_state_path,
    )

    assert result["ok"] is False
    assert result["status"] == "invalid_project_root"
    assert not db_path.exists()
    assert not index_state_path.exists()


def test_scan_skips_symlink_that_targets_file_outside_authorized_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must not be indexed", encoding="utf-8")
    link = root / "linked-secret.txt"

    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    if not link.is_symlink():
        pytest.skip("symlink creation did not produce a symlink")

    db_path = tmp_path / "state" / "project.sqlite"
    index_state_path = tmp_path / "state" / "index_state.json"
    result = scan_project(
        project_name="safe-project",
        project_root=root,
        db_path=db_path,
        index_state_path=index_state_path,
    )

    assert result["ok"] is True
    assert result["files_found"] == 1
    assert result["files_scanned"] == 0
    assert result["files_skipped"] == 1
    assert result["skipped_reasons"]["symlink"] == 1
    assert result["current_stats"]["files"] == 0

    # The test should remain valid even on platforms where link metadata differs.
    assert os.path.realpath(link) == str(outside)
