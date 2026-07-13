"""Run and render the isolated memory-ledger-v1 performance baseline."""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
from pathlib import Path

from ms8.memory.application.performance_baseline import PerformanceBudget, run_performance_baseline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--records", type=int, default=500)
    parser.add_argument("--query-iterations", type=int, default=50)
    parser.add_argument("--context-iterations", type=int, default=20)
    return parser


def main() -> int:
    args = _parser().parse_args()
    with tempfile.TemporaryDirectory(prefix="ms8-ledger-performance-") as directory:
        result = run_performance_baseline(
            Path(directory),
            record_count=args.records,
            query_iterations=args.query_iterations,
            context_iterations=args.context_iterations,
            budget=PerformanceBudget(),
        )
    payload = result.to_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    overall = "PASS" if result.budget_pass and result.query_hit_count > 0 else "FAIL"
    report = f"""# MS8 Memory Ledger v1 — Performance Baseline

- Overall result: **{overall}**
- Python: `{platform.python_version()}`
- Platform: `{platform.system()} {platform.machine()}`
- Real user runtime accessed: no
- Ledger-v1 enabled by default: no
- Dataset records: {result.record_count}
- Query iterations: {result.query_iterations}
- Context iterations: {result.context_iterations}
- Prepare seconds: {result.prepare_seconds}
- Initial ledger + projection build seconds: {result.initial_build_seconds}
- Full projection rebuild seconds: {result.rebuild_seconds}
- Query p50: {result.query_p50_ms} ms
- Query p95: {result.query_p95_ms} ms
- Context p50: {result.context_p50_ms} ms
- Context p95: {result.context_p95_ms} ms
- Query hit count: {result.query_hit_count}
- Logical state hash: `{result.logical_state_hash}`
- Budget pass: {str(result.budget_pass).lower()}
- Failed budgets: {', '.join(result.failed_budgets) if result.failed_budgets else 'none'}

The baseline runs in a disposable temporary directory and measures deterministic legacy preparation, initial ledger/projection construction, projection-backed retrieval, context assembly, and full projection rebuild.
"""
    args.output.write_text(report, encoding="utf-8")
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report)
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
