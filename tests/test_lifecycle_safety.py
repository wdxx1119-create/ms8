from __future__ import annotations

from pathlib import Path

from ms8 import lifecycle


def test_uninstall_backup_path_outside_runtime(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    (root / "memory").mkdir(parents=True, exist_ok=True)
    (root / "memory" / "auto_memory_records.jsonl").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(lifecycle, "get_runtime_dir", lambda: root)
    out = lifecycle.uninstall_runtime(dry_run=True, purge_data=True, backup=True, remove_launchd=False)
    assert out["ok"] is True
    backup_path = Path(out["backup_path"])
    assert not lifecycle._is_subpath(backup_path, root)
    assert out["backup_verified"] is True


def test_clean_reports_failed_removal(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "health").mkdir(parents=True, exist_ok=True)

    def _fake_ensure():
        return {
            "root": root,
            "logs": root / "logs",
            "health": root / "health",
            "data": root / "data",
            "backups": root / "backups",
            "memories": root / "memory" / "auto_memory_records.jsonl",
        }

    monkeypatch.setattr(lifecycle, "ensure_runtime_dirs", _fake_ensure)

    orig = lifecycle._remove_path

    def _remove_with_fail(path: Path, *, dry_run: bool):
        if path.name == "health":
            return False, "permission denied"
        return orig(path, dry_run=dry_run)

    monkeypatch.setattr(lifecycle, "_remove_path", _remove_with_fail)
    out = lifecycle.clean_runtime(dry_run=False)
    assert out["ok"] is False
    assert out["failed_count"] >= 1
    assert any(item.get("path", "").endswith("/health") for item in out["failed"])
