from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from memory.core import MemoryCore


def _print(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="openclaw-memory threshold approval CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_status = sub.add_parser("status", help="show threshold suggestion queue and monitoring stats")
    p_status.add_argument("--include-processed", action="store_true", help="include approved/rejected items")

    p_list = sub.add_parser("list", help="list pending threshold suggestions")
    p_list.add_argument("--include-processed", action="store_true", help="include approved/rejected items")

    p_gen = sub.add_parser("generate", help="generate threshold suggestions")
    p_gen.add_argument("--window", type=int, default=None, help="feedback window size")
    p_gen.add_argument("--no-enqueue", action="store_true", help="generate report only, do not queue approval")
    p_gen.add_argument("--source", default="cli", help="source tag for queue entry")

    p_approve = sub.add_parser("approve", help="approve one suggestion and apply to config")
    p_approve.add_argument("approval_id", help="approval id from list/status output")
    p_approve.add_argument("--approver", default="cli", help="approver name")
    p_approve.add_argument("--confirm", action="store_true", help="required to actually apply")

    p_reject = sub.add_parser("reject", help="reject one pending suggestion")
    p_reject.add_argument("approval_id", help="approval id from list/status output")
    p_reject.add_argument("--approver", default="cli", help="approver name")
    p_reject.add_argument("--reason", default="manual_reject", help="reject reason")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1

    core = MemoryCore(llm_enabled=False)

    if args.cmd == "status":
        pending = core.list_pending_threshold_suggestions(include_processed=bool(args.include_processed))
        mon = core.get_monitoring_status()
        payload = {
            "status": "success",
            "queue": pending,
            "monitoring_threshold_suggestion_stats": mon.get("threshold_suggestion_stats", {}),
            "maintenance_policy_stats": mon.get("maintenance_policy_stats", {}),
        }
        _print(payload)
        return 0

    if args.cmd == "list":
        _print(core.list_pending_threshold_suggestions(include_processed=bool(args.include_processed)))
        return 0

    if args.cmd == "generate":
        res = core.generate_threshold_suggestions(
            window=args.window,
            enqueue_for_approval=not bool(args.no_enqueue),
            source=str(args.source),
        )
        _print(res)
        return 0 if str(res.get("status", "")).lower() not in {"error", "failed"} else 2

    if args.cmd == "approve":
        res = core.approve_threshold_suggestion(
            approval_id=str(args.approval_id),
            approver=str(args.approver),
            confirm=bool(args.confirm),
        )
        _print(res)
        # return non-zero when explicit confirmation missing to make CI/automation safer
        if str(res.get("status", "")) == "requires_confirmation":
            return 3
        return 0 if str(res.get("status", "")).lower() not in {"error", "failed"} else 2

    if args.cmd == "reject":
        res = core.reject_threshold_suggestion(
            approval_id=str(args.approval_id),
            approver=str(args.approver),
            reason=str(args.reason),
        )
        _print(res)
        return 0 if str(res.get("status", "")).lower() not in {"error", "failed"} else 2

    print(f"unknown command: {args.cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
