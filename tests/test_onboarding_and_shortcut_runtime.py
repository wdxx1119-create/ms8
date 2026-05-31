from __future__ import annotations

import json
from pathlib import Path

from ms8 import onboarding, shortcut


def test_shortcut_install_status_remove_roundtrip(monkeypatch, tmp_path: Path) -> None:
    desktop = tmp_path / "desktop"
    monkeypatch.setenv("MS8_DESKTOP", str(desktop))

    installed = shortcut.install_shortcuts()
    assert installed["ok"] is True
    assert (desktop / "MS8.command").exists()
    assert (desktop / "MS8-Doctor.command").exists()

    status = shortcut.shortcut_status()
    assert status["main_exists"] is True
    assert status["doctor_exists"] is True

    removed = shortcut.remove_shortcuts()
    assert removed["ok"] is True
    assert not (desktop / "MS8.command").exists()
    assert not (desktop / "MS8-Doctor.command").exists()


def test_ensure_shortcuts_once_respects_disable(monkeypatch, tmp_path: Path) -> None:
    desktop = tmp_path / "desktop-disabled"
    monkeypatch.setenv("MS8_DESKTOP", str(desktop))
    monkeypatch.setenv("MS8_SHORTCUT_AUTO", "0")

    shortcut.ensure_shortcuts_once()
    assert not (desktop / "MS8.command").exists()
    assert not (desktop / "MS8-Doctor.command").exists()


def test_ensure_shortcuts_once_skips_when_already_present(monkeypatch, tmp_path: Path) -> None:
    desktop = tmp_path / "desktop-present"
    monkeypatch.setenv("MS8_DESKTOP", str(desktop))
    monkeypatch.setenv("MS8_SHORTCUT_AUTO", "1")
    desktop.mkdir(parents=True, exist_ok=True)
    (desktop / "MS8.command").write_text("ok", encoding="utf-8")
    (desktop / "MS8-Doctor.command").write_text("ok", encoding="utf-8")

    called = {"install": 0}

    def _install() -> dict:
        called["install"] += 1
        return {"ok": True}

    monkeypatch.setattr(shortcut, "install_shortcuts", _install)
    shortcut.ensure_shortcuts_once()
    assert called["install"] == 0


def test_ensure_shortcuts_once_ignores_oserror(monkeypatch, tmp_path: Path) -> None:
    desktop = tmp_path / "desktop-oserror"
    monkeypatch.setenv("MS8_DESKTOP", str(desktop))
    monkeypatch.setenv("MS8_SHORTCUT_AUTO", "1")

    monkeypatch.setattr(shortcut, "shortcut_status", lambda: {"main_exists": False, "doctor_exists": False})

    def _broken_install() -> dict:
        raise OSError("permission denied")

    monkeypatch.setattr(shortcut, "install_shortcuts", _broken_install)
    # should not raise
    shortcut.ensure_shortcuts_once()


def test_onboarding_status_invalid_json_returns_not_done(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime-invalid"
    monkeypatch.setattr(onboarding, "get_runtime_dir", lambda: runtime)
    marker = runtime / "health" / "onboarding.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{not-json", encoding="utf-8")

    status = onboarding.onboarding_status()
    assert status == {"done": False}


def test_run_onboarding_creates_marker_and_dirs(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime-create"
    monkeypatch.setattr(onboarding, "get_runtime_dir", lambda: runtime)
    monkeypatch.setenv("MS8_SHORTCUT_AUTO", "0")
    monkeypatch.setenv("MS8_CONNECT_AUTO", "0")

    result = onboarding.run_onboarding()
    assert result["ok"] is True
    assert result["skipped"] is False
    assert (runtime / "data").exists()
    assert (runtime / "backups").exists()
    assert (runtime / "logs").exists()
    assert (runtime / "health" / "onboarding.json").exists()

    marker = json.loads((runtime / "health" / "onboarding.json").read_text(encoding="utf-8"))
    assert marker["done"] is True
    assert marker["connect_bootstrap"]["skipped"] is True


def test_run_onboarding_upgrade_existing_marker(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime-upgrade"
    monkeypatch.setattr(onboarding, "get_runtime_dir", lambda: runtime)
    marker = runtime / "health" / "onboarding.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"done": True, "completed_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")

    result = onboarding.run_onboarding()
    assert result["ok"] is True
    assert result["skipped"] is False
    assert result["upgraded"] is True
    assert "connect_bootstrap_ok" in result["marker"]
