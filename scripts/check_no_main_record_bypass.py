#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_CORE = ROOT / "src" / "ms8" / "engine_core"

# Allowed direct mentions for read-only/reporting/maintenance contexts.
ALLOWLIST = {
    "src/ms8/engine_core/config.py",
    "src/ms8/engine_core/git_utils.py",
    "src/ms8/engine_core/maintenance_manager.py",
    "src/ms8/engine_core/maintenance_policy.py",
    "src/ms8/engine_core/maintenance/self_check/check_specs.py",
    "src/ms8/engine_core/maintenance/self_check/reporter.py",
    "src/ms8/engine_core/maintenance/self_repair/repair_policies.py",
    "src/ms8/engine_core/maintenance/self_repair/repair_runner.py",
    "src/ms8/engine_core/security/encryption/crypto_manager.py",
    "src/ms8/engine_core/core.py",
    "src/ms8/engine_core/record_gateway.py",
}

PATTERNS = [
    re.compile(r"auto_memory_records\.jsonl"),
    re.compile(r"records_file\.open\(\s*[\"']a[\"']"),
]


def main() -> int:
    offenders: list[str] = []
    for path in ENGINE_CORE.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not any(p.search(text) for p in PATTERNS):
            continue
        if rel in ALLOWLIST:
            continue
        offenders.append(rel)

    if offenders:
        print("Bypass risk: unexpected main-record access found:")
        for item in offenders:
            print(f"- {item}")
        return 1

    print("OK: no unexpected main-record bypass points found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
