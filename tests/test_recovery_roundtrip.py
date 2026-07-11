from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from ms8.recovery import (
    BACKUP_MANIFEST_NAME,
    create_runtime_backup,
    plan_runtime_restore,
    restore_runtime_backup,
    verify_runtime_backup,
)


def _create_runtime(root: Path) -> None:
    (root / "memory" / "logs").mkdir(parents=True)
    (root / "memory" / "auto_memory_records.jsonl").write_text(
        json.dumps({"id": "m1", "normalized_text": "hello"}) + "\n",
        encoding="utf-8",
    )
    (root / "memory" / "noncanonical_quarantine.jsonl").write_text("", encoding="utf-8")
    (root / "config.json").write_text(json.dumps({"mode": "safe"}), encoding="utf-8")
    absorb = root / "absorb"
    absorb.mkdir()
    database = absorb / "absorb.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE file_records (file_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
        connection.execute("INSERT INTO file_records VALUES (?, ?)", ("f1", "LOCAL_INDEXED"))
        connection.commit()


def _write_single_file_archive(archive: Path, relative: str, payload: bytes = b"x") -> None:
    manifest = {
        "backup_schema_version": 1,
        "files": [
            {
                "path": relative,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "snapshot_kind": "copy",
            }
        ],
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(BACKUP_MANIFEST_NAME, json.dumps(manifest))
        bundle.writestr(f"runtime/{relative}", payload)


def test_runtime_backup_restore_roundtrip_including_sqlite(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _create_runtime(source)
    target.mkdir()
    (target / "config.json").write_text(json.dumps({"mode": "old"}), encoding="utf-8")

    created = create_runtime_backup(root=source, tag="roundtrip")
    assert created["ok"] is True
    archive = Path(created["path"])
    verified = verify_runtime_backup(archive)
    assert verified["ok"] is True
    sqlite_rows = [row for row in verified["manifest"]["files"] if row["path"] == "absorb/absorb.sqlite"]
    assert sqlite_rows[0]["snapshot_kind"] == "sqlite-backup-api"

    plan = plan_runtime_restore(archive, target_root=target)
    assert plan["ok"] is True
    assert "config.json" in plan["overwrite"]
    preview = restore_runtime_backup(archive, target_root=target, apply=False)
    assert preview["applied"] is False
    assert json.loads((target / "config.json").read_text(encoding="utf-8"))["mode"] == "old"

    restored = restore_runtime_backup(archive, target_root=target, apply=True)
    assert restored["ok"] is True
    assert restored["applied"] is True
    assert Path(restored["pre_restore_backup"]).is_file()
    assert (target / "memory" / "auto_memory_records.jsonl").read_text(encoding="utf-8").endswith("\n")
    assert json.loads((target / "config.json").read_text(encoding="utf-8"))["mode"] == "safe"
    with sqlite3.connect(target / "absorb" / "absorb.sqlite") as connection:
        assert connection.execute("SELECT file_id, status FROM file_records").fetchall() == [("f1", "LOCAL_INDEXED")]
    assert (target / "memory" / "logs" / "restore_audit.jsonl").is_file()


def test_backup_verification_detects_modified_payload(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _create_runtime(root)
    archive = Path(create_runtime_backup(root=root)["path"])
    tampered = tmp_path / "tampered.zip"

    with zipfile.ZipFile(archive, "r") as source, zipfile.ZipFile(tampered, "w") as destination:
        for name in source.namelist():
            payload = source.read(name)
            if name == "runtime/config.json":
                payload = b'{"mode":"tampered"}'
            destination.writestr(name, payload)

    result = verify_runtime_backup(tampered)
    assert result["ok"] is False
    assert "checksum_mismatch:config.json" in result["errors"]


def test_backup_verification_rejects_posix_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "malicious-posix.zip"
    _write_single_file_archive(archive, "../../escape.txt")

    result = verify_runtime_backup(archive)
    assert result["ok"] is False
    assert any(error.startswith("unsafe_member:") or error.startswith("missing_or_unsafe:") for error in result["errors"])


@pytest.mark.parametrize("relative", ["C:/escape.txt", "..\\escape.txt"])
def test_backup_verification_rejects_windows_escape_paths(tmp_path: Path, relative: str) -> None:
    archive = tmp_path / "malicious-windows.zip"
    _write_single_file_archive(archive, relative)

    result = verify_runtime_backup(archive)
    assert result["ok"] is False
    assert any(error.startswith("unsafe_member:") or error.startswith("missing_or_unsafe:") for error in result["errors"])


@pytest.mark.skipif(os.name == "nt", reason="Windows hosted runners may not allow unprivileged symlink creation")
def test_restore_plan_rejects_target_symlink_escape(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    _write_single_file_archive(archive, "linked/escape.txt")
    assert verify_runtime_backup(archive)["ok"] is True

    target = tmp_path / "target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="crosses symlink"):
        plan_runtime_restore(archive, target_root=target)
