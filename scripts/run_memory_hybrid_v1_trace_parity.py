#!/usr/bin/env python3
"""Generate deterministic Hybrid Retrieval v1 plan/ranking trace fingerprints."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from ms8.memory.retrieval.trace_parity import write_trace_parity_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture deterministic hybrid plans, eligibility, scores, tie-breaks, and traces.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/memory_hybrid_v1/public_evaluation_v1.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=None)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.workspace is not None:
        report = write_trace_parity_report(
            args.fixture,
            args.output,
            workspace=args.workspace,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="ms8-hybrid-trace-") as temporary:
            report = write_trace_parity_report(
                args.fixture,
                args.output,
                workspace=Path(temporary) / "trace workspace",
            )
    print(
        json.dumps(
            {
                "ok": True,
                "schema": report["schema"],
                "output": str(args.output),
                "fingerprints": report["fingerprints"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
