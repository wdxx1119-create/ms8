from __future__ import annotations

from pathlib import Path

from ms8.memory.application.performance_baseline import PerformanceBudget, run_performance_baseline


def test_performance_baseline_is_isolated_and_logically_stable(tmp_path: Path) -> None:
    result = run_performance_baseline(
        tmp_path / "isolated",
        record_count=40,
        query_iterations=5,
        context_iterations=3,
        budget=PerformanceBudget(
            build_seconds_max=30.0,
            rebuild_seconds_max=30.0,
            query_p95_ms_max=1000.0,
            context_p95_ms_max=1500.0,
        ),
    )

    assert result.record_count == 40
    assert result.query_hit_count > 0
    assert result.ledger_head.startswith("sha256:")
    assert result.logical_state_hash.startswith("sha256:")
    assert result.prepare_seconds >= 0
    assert result.initial_build_seconds >= 0
    assert result.rebuild_seconds >= 0
    assert result.query_p95_ms >= result.query_p50_ms
    assert result.context_p95_ms >= result.context_p50_ms
    assert result.budget_pass is True
    assert result.failed_budgets == ()
