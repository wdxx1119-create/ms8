from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

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


def test_backup_verification_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.zip"
    manifest = {
        "backup_schema_version": 1,
        "files": [{"path": "../../escape.txt", "size": 1, "sha256": "0" * 64}],
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(BACKUP_MANIFEST_NAME, json.dumps(manifest))
        bundle.writestr("runtime/../../escape.txt", b"x")

    result = verify_runtime_backup(archive)
    assert result["ok"] is False
    assert any(error.startswith("unsafe_member:") or error.startswith("missing_or_unsafe:") for error in result["errors"])
