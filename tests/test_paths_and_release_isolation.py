from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ms8.paths import get_config_dir, get_data_dir, get_log_dir, get_ms8_home

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _base_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    env["MS8_HOME"] = str(tmp_path / "ms8-home")
    env["MS8_DESKTOP"] = str(tmp_path / "desktop")
    env["MS8_SHORTCUT_AUTO"] = "0"
    return env


def test_ms8_home_from_env(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "custom-home"
    monkeypatch.setenv("MS8_HOME", str(home))
    assert get_ms8_home() == home


def test_data_dir_from_env(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    data = tmp_path / "data"
    monkeypatch.setenv("MS8_HOME", str(home))
    monkeypatch.setenv("MS8_DATA_DIR", str(data))
    assert get_data_dir() == data


def test_default_home_without_env(monkeypatch) -> None:
    monkeypatch.delenv("MS8_HOME", raising=False)
    monkeypatch.delenv("MS8_DATA_DIR", raising=False)
    monkeypatch.delenv("MS8_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MS8_LOG_DIR", raising=False)
    assert get_ms8_home() == Path.home() / ".ms8"
    assert get_data_dir() == (Path.home() / ".ms8" / "data")
    assert get_config_dir() == (Path.home() / ".ms8" / "config")
    assert get_log_dir() == (Path.home() / ".ms8" / "logs")


def test_doctor_prints_current_dirs(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["MS8_DATA_DIR"] = str(tmp_path / "d")
    env["MS8_CONFIG_DIR"] = str(tmp_path / "c")
    env["MS8_LOG_DIR"] = str(tmp_path / "l")
    cp = subprocess.run([sys.executable, "-m", "ms8", "doctor"], env=env, capture_output=True, text=True)
    assert cp.returncode == 0
    assert f"MS8 home: {env['MS8_HOME']}" in cp.stdout
    assert f"Data dir: {env['MS8_DATA_DIR']}" in cp.stdout
    assert f"Config dir: {env['MS8_CONFIG_DIR']}" in cp.stdout
    assert f"Log dir: {env['MS8_LOG_DIR']}" in cp.stdout


def test_dry_run_does_not_delete_data(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    cp = subprocess.run(
        [sys.executable, "-m", "ms8", "ask", "记住 dry run 不应删除"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 0
    records = Path(env["MS8_HOME"]) / "memory" / "auto_memory_records.jsonl"
    assert records.exists()
    before = records.read_text(encoding="utf-8")
    assert before.strip()

    for cmd in (["clean", "--dry-run"], ["reset", "--dry-run"], ["uninstall", "--dry-run"]):
        cp2 = subprocess.run([sys.executable, "-m", "ms8", *cmd], env=env, capture_output=True, text=True)
        assert cp2.returncode == 0

    after = records.read_text(encoding="utf-8")
    assert after == before

