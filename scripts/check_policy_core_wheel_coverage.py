#!/usr/bin/env python3
"""Validate that the ms8-policy-core wheelhouse is publishable.

The ms8 package depends on ms8-policy-core at install time. Runtime fallback
cannot help if pip cannot find a compatible core wheel, so releases must ship
the expected Python/platform matrix before uploading ms8 itself.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_PYTHONS = ("cp310", "cp311", "cp312", "cp313")
DEFAULT_PLATFORMS = (
    "manylinux_x86_64",
    "macosx_x86_64",
    "macosx_arm64",
    "win_amd64",
)

WHEEL_RE = re.compile(
    r"^ms8_policy_core-(?P<version>[^-]+)-(?P<python>cp\d+)-(?P<abi>[^-]+)-(?P<platform>.+)\.whl$"
)


def _platform_family(platform_tag: str) -> str:
    if "manylinux" in platform_tag and platform_tag.endswith("_x86_64"):
        return "manylinux_x86_64"
    if platform_tag.startswith("macosx") and platform_tag.endswith("_x86_64"):
        return "macosx_x86_64"
    if platform_tag.startswith("macosx") and platform_tag.endswith("_arm64"):
        return "macosx_arm64"
    if platform_tag == "win_amd64":
        return "win_amd64"
    return platform_tag


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_policy_core_wheel_coverage.py <wheel> [<wheel> ...]", file=sys.stderr)
        return 2

    wheels = [Path(item) for item in argv[1:]]
    seen: set[tuple[str, str]] = set()
    versions: set[str] = set()
    malformed: list[str] = []

    for wheel in wheels:
        match = WHEEL_RE.match(wheel.name)
        if not match:
            malformed.append(wheel.name)
            continue
        versions.add(match.group("version"))
        seen.add((match.group("python"), _platform_family(match.group("platform"))))

    if malformed:
        print("[FAIL] Unexpected policy core wheel filename(s):", file=sys.stderr)
        for name in malformed:
            print(f" - {name}", file=sys.stderr)
        return 1

    if len(versions) != 1:
        print(f"[FAIL] Policy core wheels must all share one version, got: {sorted(versions)}", file=sys.stderr)
        return 1

    required = {(py, platform) for py in DEFAULT_PYTHONS for platform in DEFAULT_PLATFORMS}
    missing = sorted(required - seen)
    extra = sorted(seen - required)

    if missing:
        print("[FAIL] Incomplete ms8-policy-core wheelhouse.", file=sys.stderr)
        print(f"Expected {len(required)} wheels for Python/platform coverage.", file=sys.stderr)
        print("Missing:", file=sys.stderr)
        for py, platform in missing:
            print(f" - {py} / {platform}", file=sys.stderr)
        if extra:
            print("Extra/unrecognized coverage:", file=sys.stderr)
            for py, platform in extra:
                print(f" - {py} / {platform}", file=sys.stderr)
        print(
            "Set MS8_ALLOW_INCOMPLETE_POLICY_WHEELHOUSE=1 only for local/private testing.",
            file=sys.stderr,
        )
        return 1

    print(f"[OK] ms8-policy-core wheelhouse coverage complete: {len(required)} wheels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
