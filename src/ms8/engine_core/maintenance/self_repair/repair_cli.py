from __future__ import annotations

import argparse
import json
from typing import Any

from ...core import MemoryCore
from .repair_audit import list_repair_history, load_latest_repair_report
from .repair_orchestrator import build_repair_plan
from .repair_runner import rollback_operation, run_repair_plan


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="ms8 self-repair")
    sub = parser.add_subparsers(dest="cmd")
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--domain", default="")
    p_plan.add_argument("--check-id", default="")
    p_plan.add_argument("--risk", default="")

    p_run = sub.add_parser("run")
    p_run.add_argument("--apply", action="store_true")
    p_run.add_argument("--approve-r3", action="store_true")
    p_run.add_argument("--domain", default="")
    p_run.add_argument("--check-id", default="")
    p_run.add_argument("--risk", default="")

    sub.add_parser("status")
    p_hist = sub.add_parser("history")
    p_hist.add_argument("--limit", type=int, default=10)
    p_rb = sub.add_parser("rollback")
    p_rb.add_argument("--op", required=True)

    args = parser.parse_args()
    core = MemoryCore()
    cmd = str(getattr(args, "cmd", "") or "plan")
    if cmd == "status":
        _print(load_latest_repair_report(core.config["memory_dir"]))
        return 0
    if cmd == "history":
        _print(
            {
                "status": "ok",
                "rows": list_repair_history(core.config["memory_dir"], limit=int(args.limit)),
            }
        )
        return 0
    if cmd == "rollback":
        _print(rollback_operation(core, str(getattr(args, "op", "") or "")))
        return 0

    plan = build_repair_plan(
        core,
        mode="apply" if bool(getattr(args, "apply", False)) else "dry-run",
        only_risk=str(getattr(args, "risk", "") or ""),
        domain=str(getattr(args, "domain", "") or ""),
        check_id=str(getattr(args, "check_id", "") or ""),
    )
    if cmd == "plan":
        _print(plan)
        return 0
    if bool(getattr(args, "approve_r3", False)):
        plan["r3_approved"] = True
    out = run_repair_plan(core, plan, mode="apply" if bool(getattr(args, "apply", False)) else "dry-run")
    _print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
