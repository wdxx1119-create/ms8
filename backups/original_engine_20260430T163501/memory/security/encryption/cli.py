from __future__ import annotations

import argparse
import getpass
import json
import sys
from typing import Any, Dict

from memory.config import get_config
from memory.security import get_crypto_manager
from memory.security.recovery import recover_with_recovery_key


def _print(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="openclaw-memory security CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_sec = sub.add_parser("security", help="security operations")
    sec_sub = p_sec.add_subparsers(dest="action")
    sec_sub.add_parser("status")
    sec_sub.add_parser("lock")
    sec_sub.add_parser("unlock")
    sec_sub.add_parser("enable")
    sec_sub.add_parser("disable")
    sec_sub.add_parser("recover")

    # support short form: status/enable/... without `security`
    for action in ("status", "lock", "unlock", "enable", "disable", "recover"):
        sub.add_parser(action)

    args = parser.parse_args(argv)
    cmd = args.cmd
    action = getattr(args, "action", None)
    if cmd == "security":
        cmd = action
    if cmd is None:
        parser.print_help()
        return 1

    manager = get_crypto_manager(get_config())
    if cmd == "status":
        _print(manager.status())
        return 0
    if cmd == "lock":
        manager.lock()
        _print({"status": "success", "status_view": manager.status()})
        return 0
    if cmd == "unlock":
        pw = getpass.getpass("Master password: ")
        ok = manager.unlock(pw)
        _print({"status": "success" if ok else "error", "unlocked": ok, "status_view": manager.status()})
        return 0
    if cmd == "enable":
        pw = getpass.getpass("Set master password: ")
        res = manager.enable_encryption(pw)
        _print(res)
        rk = res.get("recovery_key")
        if rk:
            print(f"\nRecovery key (save offline): {rk}")
        return 0
    if cmd == "disable":
        pw = getpass.getpass("Master password: ")
        _print(manager.disable_encryption(pw))
        return 0
    if cmd == "recover":
        rk = getpass.getpass("Recovery key: ")
        np = getpass.getpass("New master password: ")
        _print(recover_with_recovery_key(manager, rk, np))
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
