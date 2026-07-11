"""Command-line entry point for verified backup, restore, and migrations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .format_registry import (
    CURRENT_RUNTIME_FORMAT_VERSION,
    apply_runtime_migrations,
    load_format_manifest,
    plan_runtime_migrations,
)
from .recovery import create_runtime_backup, plan_runtime_restore, restore_runtime_backup, verify_runtime_backup


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ms8-recovery", description="Verified MS8 runtime backup and recovery")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="create or verify a runtime backup")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    create = backup_sub.add_parser("create", help="create a verified runtime archive")
    create.add_argument("--root", type=_path, default=None)
    create.add_argument("--output", type=_path, default=None)
    create.add_argument("--tag", default="manual")
    verify = backup_sub.add_parser("verify", help="verify archive structure and checksums")
    verify.add_argument("archive", type=_path)

    restore = sub.add_parser("restore", help="plan or apply a restore")
    restore_sub = restore.add_subparsers(dest="restore_command", required=True)
    plan = restore_sub.add_parser("plan", help="show create/overwrite operations without writing")
    plan.add_argument("archive", type=_path)
    plan.add_argument("--target", type=_path, default=None)
    apply_parser = restore_sub.add_parser("apply", help="restore atomically after a pre-restore backup")
    apply_parser.add_argument("archive", type=_path)
    apply_parser.add_argument("--target", type=_path, default=None)
    apply_parser.add_argument("--confirm", choices=["RESTORE"], required=True)

    migrate = sub.add_parser("migrate", help="plan or apply runtime-format migrations")
    migrate_sub = migrate.add_subparsers(dest="migrate_command", required=True)
    migrate_plan = migrate_sub.add_parser("plan")
    migrate_plan.add_argument("--root", type=_path, required=True)
    migrate_plan.add_argument("--target-version", type=int, default=CURRENT_RUNTIME_FORMAT_VERSION)
    migrate_apply = migrate_sub.add_parser("apply")
    migrate_apply.add_argument("--root", type=_path, required=True)
    migrate_apply.add_argument("--target-version", type=int, default=CURRENT_RUNTIME_FORMAT_VERSION)
    migrate_apply.add_argument("--confirm", choices=["MIGRATE"], required=True)

    status = sub.add_parser("format-status", help="show the runtime format manifest")
    status.add_argument("--root", type=_path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "backup" and args.backup_command == "create":
            result = create_runtime_backup(root=args.root, output=args.output, tag=args.tag)
        elif args.command == "backup" and args.backup_command == "verify":
            result = verify_runtime_backup(args.archive)
        elif args.command == "restore" and args.restore_command == "plan":
            result = plan_runtime_restore(args.archive, target_root=args.target)
        elif args.command == "restore" and args.restore_command == "apply":
            result = restore_runtime_backup(args.archive, target_root=args.target, apply=True)
        elif args.command == "migrate" and args.migrate_command == "plan":
            result = plan_runtime_migrations(args.root, target_version=args.target_version)
        elif args.command == "migrate" and args.migrate_command == "apply":
            result = apply_runtime_migrations(args.root, target_version=args.target_version)
            result = {"ok": True, "manifest": result}
        elif args.command == "format-status":
            result = {"ok": True, "manifest": load_format_manifest(args.root)}
        else:
            result = {"ok": False, "error": "unsupported_command"}
    except (OSError, RuntimeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if bool(result.get("ok", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
