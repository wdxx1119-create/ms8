#!/usr/bin/env python3
"""Run the public Hybrid Retrieval v1 macOS reference acceptance suite."""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
from pathlib import Path

from ms8.memory.retrieval.reference_acceptance import run_reference_acceptance


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an isolated synthetic Ledger-v1 workspace and compare legacy versus hybrid-v1 retrieval.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/memory_hybrid_v1/public_evaluation_v1.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/memory_hybrid_v1_macos_acceptance"),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Optional isolated workspace. Omit to use a temporary directory.",
    )
    parser.add_argument(
        "--allow-non-macos",
        action="store_true",
        help="Generate a diagnostic report on another platform without treating it as macOS acceptance.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    current_platform = platform.system()
    if current_platform != "Darwin" and not args.allow_non_macos:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "macos_required",
                    "platform": current_platform,
                    "hint": "Use --allow-non-macos only for a non-acceptance diagnostic run.",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    if args.workspace is not None:
        artifacts = run_reference_acceptance(
            args.fixture,
            args.output_dir,
            workspace=args.workspace,
            platform_name=current_platform,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="ms8-hybrid-reference-") as temporary:
            artifacts = run_reference_acceptance(
                args.fixture,
                args.output_dir,
                workspace=Path(temporary) / "workspace",
                platform_name=current_platform,
            )

    report = dict(artifacts.report)
    print(
        json.dumps(
            {
                "ok": bool(report.get("accepted", False)),
                "accepted": bool(report.get("accepted", False)),
                "platform": report.get("platform"),
                "report_json": str(artifacts.report_json),
                "report_markdown": str(artifacts.report_markdown),
                "release_gates": report.get("release_gates", {}),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if bool(report.get("accepted", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
