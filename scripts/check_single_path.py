from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def main() -> int:
    errors: list[str] = []

    # 1) old directories must not exist
    for old_dir in (SRC / "app", SRC / "memory"):
        if old_dir.exists():
            errors.append(f"legacy_dir_exists: {old_dir}")

    # 2) forbid legacy imports
    pattern = re.compile(r"^\s*(from|import)\s+(app|memory)(\.|\s|$)")
    for py in (SRC / "ms8").rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                errors.append(f"legacy_import: {py}:{i}: {line.strip()}")

    if errors:
        print("single_path_check: FAIL")
        for e in errors:
            print(e)
        return 1

    print("single_path_check: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
