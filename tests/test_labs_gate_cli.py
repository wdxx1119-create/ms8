from __future__ import annotations

from pathlib import Path

from ms8.cli import main


def test_ops_meta_run_blocked_when_labs_disabled(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    rc = main(["ops", "meta-run"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "labs command disabled by default" in captured.err
    assert "migration hint:" in captured.err
    assert "ms8 labs meta run" in captured.err
    assert "ms8 labs status" in captured.err


def test_synthetic_command_blocked_when_labs_disabled(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    rc = main(["synthetic", "list"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "labs command disabled by default" in captured.err
    assert "migration hint:" in captured.err
    assert "ms8 labs synthetic <subcommand>" in captured.err
    assert "ms8 labs status" in captured.err
