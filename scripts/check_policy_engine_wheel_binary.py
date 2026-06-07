#!/usr/bin/env python3
"""Validate that the policy engine wheel ships native code, not policy source."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

FORBIDDEN_SUFFIXES = {
    "ms8_policy_core/engine.pyx",
    "ms8_policy_core/engine.c",
    "ms8_policy_core/engine.cpp",
}

FORBIDDEN_ENGINE_MARKERS = [
    "PII_PATTERNS",
    "BLOCK_PATTERNS",
    "LOW_VALUE_COMMANDS",
    "SEMANTIC_SHORT_ALLOWLIST",
    "def _redact_sensitive_text",
    "def _evaluate_conflict",
]


def check_wheel(wheel: Path) -> int:
    if not wheel.exists():
        print(f"[FAIL] wheel not found: {wheel}", file=sys.stderr)
        return 2

    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        engine_py = zf.read("ms8_policy_core/engine.py").decode("utf-8") if "ms8_policy_core/engine.py" in names else ""

    forbidden = sorted(name for name in names if name in FORBIDDEN_SUFFIXES)
    generated_cache = sorted(name for name in names if "__pycache__/" in name or name.endswith(".pyc"))
    native = sorted(
        name
        for name in names
        if (name.startswith("ms8_policy_core/engine.") or name.startswith("ms8_policy_core/_native."))
        and (name.endswith(".so") or name.endswith(".pyd") or name.endswith(".dylib"))
    )

    if forbidden:
        print(f"[FAIL] {wheel.name} contains forbidden source files:")
        for name in forbidden:
            print(f" - {name}")
        return 1

    if generated_cache:
        print(f"[FAIL] {wheel.name} contains generated Python cache files:")
        for name in generated_cache:
            print(f" - {name}")
        return 1

    if not native:
        print(f"[FAIL] {wheel.name} does not contain compiled engine extension")
        return 1

    if engine_py:
        leaked_markers = [marker for marker in FORBIDDEN_ENGINE_MARKERS if marker in engine_py]
        if len(engine_py.encode("utf-8")) > 6000 or leaked_markers:
            print(f"[FAIL] {wheel.name} engine.py is not a thin wrapper")
            print(f" - size={len(engine_py.encode('utf-8'))} bytes")
            for marker in leaked_markers:
                print(f" - marker={marker}")
            return 1

    print(f"[OK] {wheel.name} ships compiled engine extension only")
    for name in native:
        print(f" - {name}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_policy_engine_wheel_binary.py <wheel> [<wheel> ...]", file=sys.stderr)
        return 2

    exit_code = 0
    for item in argv[1:]:
        exit_code = max(exit_code, check_wheel(Path(item)))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
