from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ms8.engine_core.maintenance.self_check.check_specs import _current_self_check_hashes
from ms8.runtime import get_runtime_dir


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create self-check baseline update request (no auto-update).")
    parser.add_argument("--authorizer", default="pending", help="who authorizes this baseline update")
    parser.add_argument("--reason", default="authorized development change", help="why baseline update is requested")
    args = parser.parse_args()

    root = get_runtime_dir()
    reports_dir = root / "memory" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    baseline = reports_dir / "self_check_integrity_baseline.json"
    out_path = reports_dir / "baseline_update_request.json"

    current = _current_self_check_hashes()
    old_payload = _read_json(baseline)
    old_hashes = old_payload.get("hashes", {}) if isinstance(old_payload.get("hashes", {}), dict) else {}

    changed: list[dict[str, Any]] = []
    for file_name, new_hash in current.items():
        old_hash = str(old_hashes.get(file_name, ""))
        if old_hash != str(new_hash):
            src = Path(__file__).resolve().parent.parent / "src" / "ms8" / "engine_core" / "maintenance" / "self_check" / file_name
            mtime = ""
            if src.exists():
                mtime = datetime.fromtimestamp(src.stat().st_mtime, tz=timezone.utc).isoformat()
            changed.append(
                {
                    "file": str(src),
                    "old_hash": old_hash,
                    "new_hash": str(new_hash),
                    "modified_at": mtime,
                    "diff_summary": "self_check module hash changed",
                }
            )

    payload = {
        "generated_at": _utc_now(),
        "runtime_dir": str(root),
        "baseline_path": str(baseline),
        "status": "no_change" if not changed else "needs_authorization",
        "authorizer": str(args.authorizer),
        "reason": str(args.reason),
        "changes": changed,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
