from __future__ import annotations

import json
import runpy
import sys
from types import SimpleNamespace

import ms8.engine_core.config as core_config
import ms8.engine_core.security.shadow as shadow_pkg
from ms8.engine_core.security.shadow import shadow_cli


class _DummyCore:
    def shadow_seal(self, **kwargs):
        return {"status": "ok", "op": "seal", **kwargs}

    def shadow_unseal(self, *args, **kwargs):
        return {"status": "ok", "op": "unseal", "args": list(args), **kwargs}

    def shadow_verify(self):
        return {"status": "ok", "op": "verify"}

    def shadow_rotate_events_monthly(self):
        return {"status": "ok", "op": "rotate-events"}

    def shadow_sync_verified_backup(self, **kwargs):
        return {"status": "ok", "op": "backup-sync", **kwargs}

    def shadow_restore_snapshot(self, path, **kwargs):
        return {"status": "ok", "op": "restore-snapshot", "path": path, **kwargs}

    def shadow_restore_backup_snapshot(self, path, **kwargs):
        return {"status": "ok", "op": "restore-backup-snapshot", "path": path, **kwargs}

    def shadow_list_manifest_snapshots(self, limit=20):
        return {"status": "ok", "op": "manifest-snapshots", "limit": limit}

    def shadow_restore_manifest_snapshot(self, path, **kwargs):
        return {"status": "ok", "op": "restore-manifest", "path": path, **kwargs}

    def shadow_replay_spool(self, **kwargs):
        return {"status": "ok", "op": "replay", **kwargs}

    def shadow_recover_from_events(self, **kwargs):
        return {"status": "ok", "op": "recover", **kwargs}

    def shadow_recovery_drill(self, **kwargs):
        return {"status": "ok", "op": "recovery-drill", **kwargs}

    def shadow_issue_token(self, **kwargs):
        return {"status": "ok", "op": "token-issue", **kwargs}

    def shadow_revoke_token(self, token):
        return {"status": "ok", "op": "token-revoke", "token": token}


class _DummyShadow:
    class _Ledger:
        @staticmethod
        def read_spool():
            return [{"replayed": False}, {"replayed": True}, {"replayed": False}]

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


def _wire(monkeypatch) -> None:
    monkeypatch.setattr(shadow_cli, "get_config", lambda: {})
    monkeypatch.setattr(shadow_cli, "get_shadow_system", lambda _cfg: _DummyShadow())
    monkeypatch.setattr(shadow_cli, "MemoryCore", _DummyCore)


def test_shadow_cli_help_when_no_cmd(monkeypatch, capsys):
    _wire(monkeypatch)
    assert shadow_cli.main([]) == 1
    assert "usage:" in capsys.readouterr().out


def test_shadow_cli_seal_unseal_health_and_verify(monkeypatch, capsys):
    _wire(monkeypatch)
    assert shadow_cli.main(["seal", "--reason", "test", "--level", "soft"]) == 0
    assert json.loads(capsys.readouterr().out)["op"] == "seal"

    assert (
        shadow_cli.main(
            [
                "unseal",
                "--reason",
                "manual",
                "--expected-seal-reason",
                "test",
                "--expected-seal-session-id",
                "sid",
            ]
        )
        == 0
    )
    unseal = json.loads(capsys.readouterr().out)
    assert unseal["op"] == "unseal"
    assert unseal["expected_seal_reason"] == "test"

    assert shadow_cli.main(["health", "--write-probe"]) == 0
    assert json.loads(capsys.readouterr().out)["readonly"] is False

    assert shadow_cli.main(["verify"]) == 0
    assert json.loads(capsys.readouterr().out)["op"] == "verify"


def test_shadow_cli_reset_rotate_and_backup(monkeypatch, capsys):
    _wire(monkeypatch)
    assert shadow_cli.main(["reset-checkpoint"]) == 0
    assert json.loads(capsys.readouterr().out)["reset"] is True

    assert shadow_cli.main(["rotate-events"]) == 0
    assert json.loads(capsys.readouterr().out)["op"] == "rotate-events"

    assert shadow_cli.main(["backup-sync", "--caller-id", "ops", "--token", "tok"]) == 0
    backup = json.loads(capsys.readouterr().out)
    assert backup["op"] == "backup-sync"
    assert backup["caller_id"] == "ops"


def test_shadow_cli_restore_manifest_and_search(monkeypatch, capsys):
    _wire(monkeypatch)
    assert shadow_cli.main(["restore-snapshot", "/tmp/a", "--caller-id", "ops"]) == 0
    assert json.loads(capsys.readouterr().out)["op"] == "restore-snapshot"

    assert shadow_cli.main(["restore-backup-snapshot", "/tmp/b", "--caller-id", "ops"]) == 0
    assert json.loads(capsys.readouterr().out)["op"] == "restore-backup-snapshot"

    assert shadow_cli.main(["manifest-snapshots", "--limit", "7"]) == 0
    assert json.loads(capsys.readouterr().out)["limit"] == 7

    assert shadow_cli.main(["restore-manifest", "/tmp/m"]) == 0
    assert json.loads(capsys.readouterr().out)["op"] == "restore-manifest"

    assert shadow_cli.main(["search", "abc", "--limit", "3"]) == 0
    results = json.loads(capsys.readouterr().out)["results"]
    assert results[0]["query"] == "abc"


def test_shadow_cli_replay_recover_recovery_drill(monkeypatch, capsys):
    _wire(monkeypatch)
    assert shadow_cli.main(["replay"]) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["op"] == "replay"
    assert replay["caller_id"] == "trusted_cli"

    assert shadow_cli.main(["recover", "--from", "2026-01-01"]) == 0
    recover = json.loads(capsys.readouterr().out)
    assert recover["op"] == "recover"
    assert recover["since_ts"] == "2026-01-01"

    assert shadow_cli.main(["recovery-drill", "--sample-text", "hi"]) == 0
    drill = json.loads(capsys.readouterr().out)
    assert drill["op"] == "recovery-drill"
    assert drill["sample_text"] == "hi"


def test_shadow_cli_token_issue_and_revoke(monkeypatch, capsys):
    _wire(monkeypatch)
    assert shadow_cli.main(["token-issue", "--permissions", "shadow:verify,shadow:recover"]) == 0
    issued = json.loads(capsys.readouterr().out)
    assert issued["op"] == "token-issue"
    assert issued["permissions"] == ["shadow:recover", "shadow:verify"]

    assert shadow_cli.main(["token-issue"]) == 0
    default_issued = json.loads(capsys.readouterr().out)
    assert default_issued["op"] == "token-issue"
    assert "shadow:recover" in default_issued["permissions"]

    assert shadow_cli.main(["token-revoke", "abc"]) == 0
    revoked = json.loads(capsys.readouterr().out)
    assert revoked["op"] == "token-revoke"
    assert revoked["token"] == "abc"


def test_shadow_cli_unknown_command_branch(monkeypatch, capsys):
    _wire(monkeypatch)

    def _fake_parse_args(_self, _argv=None):
        return SimpleNamespace(cmd="unknown_cmd")

    monkeypatch.setattr(shadow_cli.argparse.ArgumentParser, "parse_args", _fake_parse_args)
    assert shadow_cli.main(["ignored"]) == 2
    assert "unknown command: unknown_cmd" in capsys.readouterr().err


def test_shadow_cli_module_entrypoint_line(monkeypatch, capsys):
    monkeypatch.setattr(core_config, "get_config", lambda: {})
    monkeypatch.setattr(shadow_pkg, "get_shadow_system", lambda _cfg: _DummyShadow())
    monkeypatch.setattr(shadow_cli, "MemoryCore", _DummyCore)
    monkeypatch.setattr(sys, "argv", ["shadow_cli.py", "status"])
    try:
        runpy.run_module("ms8.engine_core.security.shadow.shadow_cli", run_name="__main__")
    except SystemExit as exc:
        assert int(exc.code) == 0
    else:
        raise AssertionError("expected SystemExit from module entrypoint")
    assert "\"status\": \"ok\"" in capsys.readouterr().out
