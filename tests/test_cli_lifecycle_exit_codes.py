from __future__ import annotations

from ms8 import cli


def test_clean_exit_code_follows_result_ok(monkeypatch) -> None:
    monkeypatch.setattr(cli, "onboarding_status", lambda: {"done": True})
    monkeypatch.setattr(cli, "ensure_shortcuts_once", lambda: None)
    monkeypatch.setattr(cli, "clean_runtime", lambda dry_run: {"ok": False, "operation": "clean"})
    code = cli.main(["clean", "--dry-run"])
    assert code == 1


def test_reset_exit_code_follows_result_ok(monkeypatch) -> None:
    monkeypatch.setattr(cli, "onboarding_status", lambda: {"done": True})
    monkeypatch.setattr(cli, "ensure_shortcuts_once", lambda: None)
    monkeypatch.setattr(cli, "reset_runtime", lambda dry_run, backup: {"ok": False, "operation": "reset"})
    code = cli.main(["reset", "--dry-run"])
    assert code == 1


def test_uninstall_exit_code_follows_result_ok(monkeypatch) -> None:
    monkeypatch.setattr(cli, "onboarding_status", lambda: {"done": True})
    monkeypatch.setattr(cli, "ensure_shortcuts_once", lambda: None)
    monkeypatch.setattr(
        cli,
        "uninstall_runtime",
        lambda dry_run, purge_data, backup, remove_launchd: {"ok": False, "operation": "uninstall"},
    )
    code = cli.main(["uninstall", "--dry-run"])
    assert code == 1
