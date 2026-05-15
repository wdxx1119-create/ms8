from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from ms8.record_policy import repair_scope_flags
from ms8.runtime import ensure_runtime_dirs


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="P0-2 governance backfill migration")
    parser.add_argument("--mode", choices=["dry-run", "apply", "verify"], default="dry-run")
    args = parser.parse_args()

    paths = ensure_runtime_dirs()
    records_file = paths["memories"]
    health_dir = paths["health"]
    health_dir.mkdir(parents=True, exist_ok=True)
    report_path = health_dir / "migration_report.json"

    if args.mode == "dry-run":
        stats = repair_scope_flags(records_file, dry_run=True)
    elif args.mode == "apply":
        stats = repair_scope_flags(records_file, dry_run=False)
    else:
        stats = repair_scope_flags(records_file, dry_run=True)

    report = {
        "at": _utc_now(),
        "mode": args.mode,
        "records_file": str(records_file),
        "stats": stats,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
