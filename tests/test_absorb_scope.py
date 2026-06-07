from __future__ import annotations

from pathlib import Path

from ms8.absorb.scope import add_allowed_root, is_path_allowed, list_allowed_roots, remove_allowed_root


def test_absorb_scope_add_remove_and_allow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    file_path = root / "note.md"
    file_path.write_text("hello", encoding="utf-8")

    out = add_allowed_root(root)
    assert out["ok"] is True
    assert str(root.resolve()) in list_allowed_roots()
    assert is_path_allowed(file_path) is True

    removed = remove_allowed_root(root)
    assert removed["status"] == "removed"
    assert is_path_allowed(file_path) is False


def test_absorb_scope_rejects_high_risk_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))

    out = add_allowed_root(Path.home())
    assert out["ok"] is False
    assert out["reason"] == "high_risk_path_requires_confirmation"


def test_absorb_scope_rejects_hidden_segment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    hidden = root / ".secret"
    hidden.mkdir(parents=True)
    file_path = hidden / "note.md"
    file_path.write_text("hidden", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert is_path_allowed(file_path) is False
