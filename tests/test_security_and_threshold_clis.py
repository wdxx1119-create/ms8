from __future__ import annotations

import argparse
import json

from ms8.engine_core import threshold_cli
from ms8.engine_core.security.encryption import cli as encryption_cli
from ms8.engine_core.security.shadow import shadow_cli


class _DummyCore:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def list_pending_threshold_suggestions(self, include_processed=False):
        self.calls.append(("list_pending_threshold_suggestions", include_processed))
        return [{"id": "a1", "status": "pending"}]

    def get_monitoring_status(self):
        self.calls.append(("get_monitoring_status",))
        return {"threshold_suggestion_stats": {"pending": 1}, "maintenance_policy_stats": {"ok": True}}

    def generate_threshold_suggestions(self, window=None, enqueue_for_approval=True, source="cli"):
        self.calls.append(("generate_threshold_suggestions", window, enqueue_for_approval, source))
        return {"status": "success"}

    def approve_threshold_suggestion(self, approval_id, approver, confirm=False):
        self.calls.append(("approve_threshold_suggestion", approval_id, approver, confirm))
        if not confirm:
            return {"status": "requires_confirmation"}
        return {"status": "success"}

    def reject_threshold_suggestion(self, approval_id, approver, reason="manual_reject"):
        self.calls.append(("reject_threshold_suggestion", approval_id, approver, reason))
        return {"status": "success"}

    # shadow core calls
    def shadow_seal(self, **kwargs):
        return {"status": "ok", "op": "seal", **kwargs}

    def shadow_unseal(self, *args, **kwargs):
        return {"status": "ok", "op": "unseal"}

    def shadow_verify(self):
        return {"status": "ok", "op": "verify"}

    def shadow_rotate_events_monthly(self):
        return {"status": "ok", "op": "rotate-events"}

    def shadow_sync_verified_backup(self, **kwargs):
        return {"status": "ok", "op": "backup-sync"}

    def shadow_restore_snapshot(self, *args, **kwargs):
        return {"status": "ok", "op": "restore-snapshot"}

    def shadow_restore_backup_snapshot(self, *args, **kwargs):
        return {"status": "ok", "op": "restore-backup-snapshot"}

    def shadow_list_manifest_snapshots(self, limit=20):
        return {"status": "ok", "items": [], "limit": limit}

    def shadow_restore_manifest_snapshot(self, *args, **kwargs):
        return {"status": "ok", "op": "restore-manifest"}

    def shadow_replay_spool(self, **kwargs):
        return {"status": "ok", "op": "replay"}

    def shadow_recover_from_events(self, **kwargs):
        return {"status": "ok", "op": "recover"}

    def shadow_recovery_drill(self, **kwargs):
        return {"status": "ok", "op": "recovery-drill"}

    def shadow_issue_token(self, **kwargs):
        return {"status": "ok", "op": "token-issue"}

    def shadow_revoke_token(self, token):
        return {"status": "ok", "op": "token-revoke", "token": token}


class _DummyShadow:
    class _Ledger:
        @staticmethod
        def read_spool():
            return [{"replayed": False}, {"replayed": True}]

    ledger = _Ledger()

    @staticmethod
    def status(verbose=False, history_limit=50):
        return {"status": "ok", "verbose": verbose, "history_limit": history_limit}

    @staticmethod
    def health_check(readonly=True):
        return {"status": "ok", "readonly": readonly}

    @staticmethod
    def reset_checkpoint():
        return {"status": "ok", "reset": True}

    @staticmethod
    def search_shadow(query, limit=5):
        return [{"query": query, "limit": limit}]


class _DummyCrypto:
    def status(self):
        return {"enabled": False}

    def lock(self):
        return None

    def unlock(self, _pw):
        return True

    def enable_encryption(self, _pw):
        return {"status": "success", "recovery_key": "rk-test"}

    def disable_encryption(self, _pw):
        return {"status": "success"}


def test_threshold_cli_status_and_approve(monkeypatch, capsys):
    monkeypatch.setattr(threshold_cli, "MemoryCore", _DummyCore)

    code = threshold_cli.main(["status"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert "queue" in payload

    code = threshold_cli.main(["approve", "a1"])
    assert code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "requires_confirmation"

    code = threshold_cli.main(["approve", "a1", "--confirm"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"


def test_encryption_cli_short_commands(monkeypatch, capsys):
    monkeypatch.setattr(encryption_cli, "get_config", lambda: {})
    monkeypatch.setattr(encryption_cli, "get_crypto_manager", lambda _cfg: _DummyCrypto())
    monkeypatch.setattr(encryption_cli.getpass, "getpass", lambda _p="": "pw")
    monkeypatch.setattr(
        encryption_cli,
        "recover_with_recovery_key",
        lambda _m, _rk, _np: {"status": "success", "recovered": True},
    )

    assert encryption_cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False

    assert encryption_cli.main(["enable"]) == 0
    out = capsys.readouterr().out
    assert '"status": "success"' in out
    assert "Recovery key" in out

    assert encryption_cli.main(["recover"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"


def test_encryption_cli_more_branches(monkeypatch, capsys):
    monkeypatch.setattr(encryption_cli, "get_config", lambda: {})
    monkeypatch.setattr(encryption_cli, "get_crypto_manager", lambda _cfg: _DummyCrypto())
    monkeypatch.setattr(encryption_cli.getpass, "getpass", lambda _p="": "pw")
    monkeypatch.setattr(
        encryption_cli,
        "recover_with_recovery_key",
        lambda _m, _rk, _np: {"status": "success", "recovered": True},
    )

    # no command -> help + exit code 1
    assert encryption_cli.main([]) == 1
    _ = capsys.readouterr()

    # lock / unlock / disable paths
    assert encryption_cli.main(["lock"]) == 0
    lock_payload = json.loads(capsys.readouterr().out)
    assert lock_payload["status"] == "success"

    assert encryption_cli.main(["unlock"]) == 0
    unlock_payload = json.loads(capsys.readouterr().out)
    assert unlock_payload["status"] in {"success", "error"}
    assert "unlocked" in unlock_payload

    assert encryption_cli.main(["disable"]) == 0
    disable_payload = json.loads(capsys.readouterr().out)
    assert disable_payload["status"] == "success"

    # unknown command branch
    class _Args:
        cmd = "unknown-action"
        action = None

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self, argv=None: _Args())  # type: ignore[method-assign]
    assert encryption_cli.main(["ignored"]) == 2
    err = capsys.readouterr().err
    assert "unknown command" in err


def test_shadow_cli_selected_commands(monkeypatch, capsys):
    monkeypatch.setattr(shadow_cli, "get_config", lambda: {})
    monkeypatch.setattr(shadow_cli, "get_shadow_system", lambda _cfg: _DummyShadow())
    monkeypatch.setattr(shadow_cli, "MemoryCore", _DummyCore)

    assert shadow_cli.main(["status", "--verbose", "--history-limit", "7"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verbose"] is True
    assert payload["history_limit"] == 7

    assert shadow_cli.main(["replay", "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert payload["pending"] == 1

    assert shadow_cli.main(["token-issue", "--preset", "ops_readonly"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["op"] == "token-issue"
