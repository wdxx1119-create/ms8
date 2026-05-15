from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ms8"


def _all_python_sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if p.is_file())


def test_no_forbidden_exception_catches() -> None:
    forbidden_patterns = {
        "except Exception": re.compile(r"except\s+Exception(\s+as\s+\w+)?\s*:"),
        "except BaseException": re.compile(r"except\s+BaseException(\s+as\s+\w+)?\s*:"),
        "bare except": re.compile(r"except\s*:\s*$", re.MULTILINE),
        "except...pass": re.compile(r"except[^\n]*:\n\s+pass\b", re.MULTILINE),
    }

    violations: list[str] = []
    for path in _all_python_sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(ROOT)
        for label, pattern in forbidden_patterns.items():
            if pattern.search(text):
                violations.append(f"{rel}: {label}")

    assert not violations, "Forbidden exception patterns found:\n" + "\n".join(violations)
