from __future__ import annotations

from pathlib import Path

import ms8.paths as paths


def test_get_ms8_home_prefers_legacy_when_legacy_score_higher(monkeypatch, tmp_path: Path) -> None:
    modern = tmp_path / ".ms8"
    legacy = tmp_path / ".ms8_runtime"
    (modern / "memory").mkdir(parents=True, exist_ok=True)
    (legacy / "memory").mkdir(parents=True, exist_ok=True)
    # modern score = 1
    (modern / "memory" / "auto_memory_index.json").write_text("{}", encoding="utf-8")
    # legacy score = 4
    (legacy / "memory" / "auto_memory_records.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(paths.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("MS8_HOME", raising=False)
    assert paths.get_ms8_home() == legacy


def test_get_ms8_home_prefers_existing_modern_when_legacy_missing(monkeypatch, tmp_path: Path) -> None:
    modern = tmp_path / ".ms8"
    modern.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("MS8_HOME", raising=False)
    assert paths.get_ms8_home() == modern


def test_get_ms8_home_prefers_existing_legacy_when_modern_missing(monkeypatch, tmp_path: Path) -> None:
    legacy = tmp_path / ".ms8_runtime"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("MS8_HOME", raising=False)
    assert paths.get_ms8_home() == legacy


def test_env_path_ignores_blank_values(monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", "   ")
    assert paths._env_path("MS8_HOME") is None
