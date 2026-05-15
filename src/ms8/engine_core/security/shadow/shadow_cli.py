from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ...config import get_config
from ...core import MemoryCore
from . import get_shadow_system

TOKEN_PRESETS = {
    "ops_readonly": ["shadow:verify"],
    "ops_recover": ["shadow:verify", "shadow:replay", "shadow:recover"],
    "ops_admin": [
        "seal:trigger",
        "seal:clear",
        "shadow:verify",
        "shadow:replay",
        "shadow:recover",
        "shadow:backup_sync",
        "shadow:restore_snapshot",
        "shadow:manifest_restore",
        "shadow:restore_backup_snapshot",
    ],
}


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _add_auth_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--caller-id", default="trusted_cli")
    p.add_argument("--token", default="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ms8 shadow CLI")
    sub = parser.add_subparsers(dest="cmd")
    p_status = sub.add_parser("status")
    p_status.add_argument("--verbose", action="store_true")
    p_status.add_argument("--history-limit", type=int, default=50)
    p_seal = sub.add_parser("seal")
    p_seal.add_argument("--reason", default="manual")
    p_seal.add_argument("--level", default="hard", choices=["soft", "hard"])
    _add_auth_args(p_seal)
    p_unseal = sub.add_parser("unseal")
    p_unseal.add_argument("--reason", default="manual")
    p_unseal.add_argument("--expected-seal-reason", default="")
    p_unseal.add_argument("--expected-seal-session-id", default="")
    _add_auth_args(p_unseal)
    p_replay = sub.add_parser("replay")
    p_replay.add_argument("--dry-run", action="store_true")
    _add_auth_args(p_replay)
    p_recover = sub.add_parser("recover")
    p_recover.add_argument("--from", dest="since_ts", default="")
    _add_auth_args(p_recover)
    p_health = sub.add_parser("health")
    p_health.add_argument("--write-probe", action="store_true", help="append explicit health probe event")
    sub.add_parser("verify")
    sub.add_parser("reset-checkpoint")
    sub.add_parser("rotate-events")
    p_bs = sub.add_parser("backup-sync")
    _add_auth_args(p_bs)
    p_restore = sub.add_parser("restore-snapshot")
    p_restore.add_argument("path")
    _add_auth_args(p_restore)
    p_restore_b = sub.add_parser("restore-backup-snapshot")
    p_restore_b.add_argument("path")
    _add_auth_args(p_restore_b)
    p_list_m = sub.add_parser("manifest-snapshots")
    p_list_m.add_argument("--limit", type=int, default=20)
    p_restore_m = sub.add_parser("restore-manifest")
    p_restore_m.add_argument("path")
    _add_auth_args(p_restore_m)
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=5)
    p_drill = sub.add_parser("recovery-drill")
    p_drill.add_argument("--sample-text", default="shadow_recovery_drill_sample")
    _add_auth_args(p_drill)
    p_tok_i = sub.add_parser("token-issue")
    p_tok_i.add_argument("--caller-id", default="trusted_cli")
    p_tok_i.add_argument(
        "--preset",
        default="",
        choices=["", "ops_readonly", "ops_recover", "ops_admin"],
        help="optional permission preset",
    )
    p_tok_i.add_argument(
        "--permissions",
        default="",
        help="comma-separated permission list",
    )
    p_tok_i.add_argument("--ttl-seconds", type=int, default=1800)
    p_tok_r = sub.add_parser("token-revoke")
    p_tok_r.add_argument("token")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1

    cfg = get_config()
    shadow = get_shadow_system(cfg)
    if args.cmd == "status":
        _print(
            shadow.status(
                verbose=bool(getattr(args, "verbose", False)),
                history_limit=int(getattr(args, "history_limit", 50)),
            )
        )
        return 0
    if args.cmd == "seal":
        core = MemoryCore()
        _print(
            core.shadow_seal(
                reason=str(args.reason),
                level=str(args.level),
                caller_id=str(args.caller_id),
                request_token=str(args.token),
            )
        )
        return 0
    if args.cmd == "unseal":
        core = MemoryCore()
        _print(
            core.shadow_unseal(
                str(args.reason),
                caller_id=str(args.caller_id),
                request_token=str(args.token),
                expected_seal_reason=str(getattr(args, "expected_seal_reason", "") or ""),
                expected_seal_session_id=str(getattr(args, "expected_seal_session_id", "") or ""),
            )
        )
        return 0
    if args.cmd == "health":
        _print(shadow.health_check(readonly=not bool(getattr(args, "write_probe", False))))
        return 0
    if args.cmd == "verify":
        core = MemoryCore()
        _print(core.shadow_verify())
        return 0
    if args.cmd == "reset-checkpoint":
        _print(shadow.reset_checkpoint())
        return 0
    if args.cmd == "rotate-events":
        core = MemoryCore()
        _print(core.shadow_rotate_events_monthly())
        return 0
    if args.cmd == "backup-sync":
        core = MemoryCore()
        _print(
            core.shadow_sync_verified_backup(
                caller_id=str(args.caller_id),
                request_token=str(args.token),
            )
        )
        return 0
    if args.cmd == "restore-snapshot":
        core = MemoryCore()
        _print(
            core.shadow_restore_snapshot(
                str(args.path),
                caller_id=str(args.caller_id),
                request_token=str(args.token),
            )
        )
        return 0
    if args.cmd == "restore-backup-snapshot":
        core = MemoryCore()
        _print(
            core.shadow_restore_backup_snapshot(
                str(args.path),
                caller_id=str(args.caller_id),
                request_token=str(args.token),
            )
        )
        return 0
    if args.cmd == "manifest-snapshots":
        core = MemoryCore()
        _print(core.shadow_list_manifest_snapshots(limit=int(args.limit)))
        return 0
    if args.cmd == "restore-manifest":
        core = MemoryCore()
        _print(
            core.shadow_restore_manifest_snapshot(
                str(args.path),
                caller_id=str(args.caller_id),
                request_token=str(args.token),
            )
        )
        return 0
    if args.cmd == "replay":
        if bool(getattr(args, "dry_run", False)):
            rows = shadow.ledger.read_spool()
            pending = sum(1 for r in rows if not bool(r.get("replayed", False)))
            _print({"status": "dry_run", "total": len(rows), "pending": pending})
            return 0
        core = MemoryCore()
        _print(
            core.shadow_replay_spool(
                caller_id=str(args.caller_id),
                request_token=str(args.token),
            )
        )
        return 0
    if args.cmd == "recover":
        core = MemoryCore()
        _print(
            core.shadow_recover_from_events(
                since_ts=str(getattr(args, "since_ts", "") or ""),
                caller_id=str(args.caller_id),
                request_token=str(args.token),
            )
        )
        return 0
    if args.cmd == "search":
        _print({"status": "ok", "results": shadow.search_shadow(args.query, limit=int(args.limit))})
        return 0
    if args.cmd == "recovery-drill":
        core = MemoryCore()
        _print(
            core.shadow_recovery_drill(
                caller_id=str(args.caller_id),
                request_token=str(args.token),
                sample_text=str(getattr(args, "sample_text", "")),
            )
        )
        return 0
    if args.cmd == "token-issue":
        core = MemoryCore()
        custom = [x.strip() for x in str(getattr(args, "permissions", "")).split(",") if x.strip()]
        preset_name = str(getattr(args, "preset", "") or "")
        preset_perms = list(TOKEN_PRESETS.get(preset_name, []))
        perms = sorted(set(preset_perms + custom))
        if not perms:
            perms = list(TOKEN_PRESETS["ops_admin"])
        _print(
            core.shadow_issue_token(
                caller_id=str(getattr(args, "caller_id", "trusted_cli")),
                permissions=perms,
                ttl_seconds=int(getattr(args, "ttl_seconds", 1800)),
            )
        )
        return 0
    if args.cmd == "token-revoke":
        core = MemoryCore()
        _print(core.shadow_revoke_token(str(getattr(args, "token", ""))))
        return 0

    print(f"unknown command: {args.cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
