#!/usr/bin/env python3
"""Run Hybrid Retrieval v1 frozen-contract parity on Windows."""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
from pathlib import Path

from ms8.memory.retrieval.platform_parity import run_windows_parity


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the frozen Hybrid Retrieval v1 ordering and Windows IO boundaries.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/memory_hybrid_v1/public_evaluation_v1.json"),
    )
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("tests/fixtures/memory_hybrid_v1/public_contract_v1.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/memory_hybrid_v1_windows_parity"),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Isolated workspace path. The Windows CI path intentionally includes Unicode and spaces.",
    )
    parser.add_argument(
        "--allow-non-windows",
        action="store_true",
        help="Run a diagnostic on another platform without claiming Windows parity.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    current_platform = platform.system()
    if current_platform != "Windows" and not args.allow_non_windows:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "windows_required",
                    "platform": current_platform,
                    "hint": "Use --allow-non-windows only for a diagnostic run.",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    platform_label = "Windows" if current_platform == "Windows" else current_platform
    if args.workspace is not None:
        artifacts = run_windows_parity(
            args.fixture,
            args.contract,
            args.output_dir,
            workspace=args.workspace,
            platform_name=platform_label,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="ms8-hybrid-windows-") as temporary:
            workspace = Path(temporary) / "MS8 Hybrid 中文 workspace"
            artifacts = run_windows_parity(
                args.fixture,
                args.contract,
                args.output_dir,
                workspace=workspace,
                platform_name=platform_label,
            )

    report = artifacts.report
    print(
        json.dumps(
            {
                "ok": bool(report.get("accepted", False)),
                "accepted": bool(report.get("accepted", False)),
                "platform": report.get("platform"),
                "report_json": str(artifacts.report_json),
                "report_markdown": str(artifacts.report_markdown),
                "gates": report.get("gates", {}),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if bool(report.get("accepted", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
