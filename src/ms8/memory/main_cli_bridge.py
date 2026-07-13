"""Translate ``ms8 memory-ledger`` argparse namespaces to the guarded operations CLI."""

from __future__ import annotations

from argparse import Namespace

from .operations_cli import main as run_operations_cli


def _append_option(argv: list[str], flag: str, value: object, *, include_empty: bool = False) -> None:
    text = str(value or "")
    if text or include_empty:
        argv.extend((flag, text))


def run_memory_ledger_operations(args: Namespace) -> int:
    workspace = str(getattr(args, "workspace", "") or "")
    command = str(getattr(args, "memory_ledger_cmd", "") or "")
    argv = ["--workspace", workspace, command]

    if command in {"query", "context"}:
        argv.append(str(getattr(args, "text", "") or ""))
        argv.extend(("--limit", str(getattr(args, "limit", 5) or 5)))
        _append_option(argv, "--recorded-as-of", getattr(args, "recorded_as_of", ""))
        _append_option(argv, "--valid-at", getattr(args, "valid_at", ""))
        _append_option(argv, "--realm-id", getattr(args, "realm_id", ""))
        _append_option(argv, "--scope", getattr(args, "scope", ""))
    elif command == "explain":
        argv.append(str(getattr(args, "claim_id", "") or ""))
    elif command == "rebuild":
        rebuild_command = str(getattr(args, "ledger_rebuild_cmd", "") or "")
        argv.append(rebuild_command)
        if rebuild_command == "apply":
            _append_option(argv, "--expected-head", getattr(args, "expected_head", ""), include_empty=True)
            if bool(getattr(args, "apply", False)):
                argv.append("--apply")
            _append_option(argv, "--confirm", getattr(args, "confirm", ""))
    elif command == "mutate":
        argv.append(str(getattr(args, "action", "") or ""))
        for flag, attribute in (
            ("--claim-id", "claim_id"),
            ("--replacement-json", "replacement_json"),
            ("--conflict-id", "conflict_id"),
            ("--winning-claim-id", "winning_claim_id"),
            ("--expected-head", "expected_head"),
            ("--recorded-at", "recorded_at"),
            ("--reason", "reason"),
            ("--actor-kind", "actor_kind"),
            ("--actor-id", "actor_id"),
            ("--confirm", "confirm"),
        ):
            _append_option(argv, flag, getattr(args, attribute, ""))
        for claim_id in getattr(args, "claim_ids", []) or []:
            _append_option(argv, "--claim-ids", claim_id)
        if bool(getattr(args, "apply", False)):
            argv.append("--apply")
    elif command == "migrate":
        migrate_command = str(getattr(args, "ledger_migrate_cmd", "") or "")
        argv.append(migrate_command)
        if migrate_command in {"plan", "apply"}:
            for flag, attribute in (
                ("--source-jsonl", "source_jsonl"),
                ("--migration-id", "migration_id"),
                ("--recorded-at", "recorded_at"),
            ):
                _append_option(argv, flag, getattr(args, attribute, ""), include_empty=True)
            if migrate_command == "apply":
                _append_option(argv, "--backup-id", getattr(args, "backup_id", ""), include_empty=True)
        elif migrate_command == "rollback":
            _append_option(argv, "--backup-path", getattr(args, "backup_path", ""), include_empty=True)
            _append_option(argv, "--expected-head", getattr(args, "expected_head", ""), include_empty=True)
        for target in getattr(args, "backup_target", []) or []:
            _append_option(argv, "--backup-target", target)
        if bool(getattr(args, "apply", False)):
            argv.append("--apply")
        _append_option(argv, "--confirm", getattr(args, "confirm", ""))
    return run_operations_cli(argv)


__all__ = ["run_memory_ledger_operations"]
