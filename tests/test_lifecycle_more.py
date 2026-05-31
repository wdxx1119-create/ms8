from __future__ import annotations

from pathlib import Path

from ms8 import lifecycle


def test_remove_path_missing_and_file_success(tmp_path: Path) -> None:
    missing = tmp_path / "none"
    ok, err = lifecycle._remove_path(missing, dry_run=False)
    assert ok is False
    assert err == ""

    f = tmp_path / "x.txt"
    f.write_text("x", encoding="utf-8")
    ok, err = lifecycle._remove_path(f, dry_run=False)
    assert ok is True
    assert err == ""
    assert not f.exists()


def test_copy_if_exists_file_and_dir(tmp_path: Path) -> None:
    src_file = tmp_path / "a.txt"
    dst_file = tmp_path / "out" / "a.txt"
    src_file.write_text("hello", encoding="utf-8")
    assert lifecycle._copy_if_exists(src_file, dst_file, dry_run=False) is True
    assert dst_file.read_text(encoding="utf-8") == "hello"

    src_dir = tmp_path / "dir"
    src_dir.mkdir()
    (src_dir / "k.txt").write_text("v", encoding="utf-8")
    dst_dir = tmp_path / "dst_dir"
    assert lifecycle._copy_if_exists(src_dir, dst_dir, dry_run=False) is True
    assert (dst_dir / "k.txt").read_text(encoding="utf-8") == "v"


def test_uninstall_calls_remove_service_when_not_dry_run(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "health").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "connect").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(lifecycle, "get_runtime_dir", lambda: root)
    monkeypatch.setattr(lifecycle, "remove_service", lambda: {"ok": True, "removed": True})

    out = lifecycle.uninstall_runtime(
        dry_run=False,
        purge_data=False,
        backup=False,
        remove_launchd=True,
    )
    assert out["ok"] is True
    assert out["service"]["ok"] is True
    assert out["runtime_root_removed"] is False
    assert out["removed_count"] >= 1


def test_render_lifecycle_result_json() -> None:
    text = lifecycle.render_lifecycle_result({"ok": True, "operation": "clean"})
    assert '"operation": "clean"' in text
