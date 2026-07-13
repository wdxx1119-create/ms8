"""Deterministic, isolated performance baseline for memory-ledger-v1.

The baseline uses a temporary ledger and projections only. It records timing evidence
without touching a user runtime or enabling ledger-v1 in production.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..infrastructure.fts_projection import FtsProjectionAdapter
from ..infrastructure.graph_projection import GraphProjectionAdapter
from ..infrastructure.jsonl_ledger import JsonlRecordStore
from ..infrastructure.search_projection import SearchProjectionAdapter
from ..infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter
from ..infrastructure.vector_projection import VectorProjectionAdapter
from .legacy_migration import LegacyMigrationStagingService, prepare_legacy_migration
from .projection_service import ProjectionCoordinator
from .retrieval_context import ContextAssembler, RetrievalEngine, RetrievalRequest


@dataclass(frozen=True, slots=True)
class PerformanceBudget:
    build_seconds_max: float = 30.0
    rebuild_seconds_max: float = 30.0
    query_p95_ms_max: float = 500.0
    context_p95_ms_max: float = 750.0


@dataclass(frozen=True, slots=True)
class PerformanceBaselineResult:
    record_count: int
    query_iterations: int
    context_iterations: int
    prepare_seconds: float
    initial_build_seconds: float
    rebuild_seconds: float
    query_p50_ms: float
    query_p95_ms: float
    context_p50_ms: float
    context_p95_ms: float
    ledger_head: str
    logical_state_hash: str
    query_hit_count: int
    budget_pass: bool
    failed_budgets: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "record_count": self.record_count,
            "query_iterations": self.query_iterations,
            "context_iterations": self.context_iterations,
            "prepare_seconds": self.prepare_seconds,
            "initial_build_seconds": self.initial_build_seconds,
            "rebuild_seconds": self.rebuild_seconds,
            "query_p50_ms": self.query_p50_ms,
            "query_p95_ms": self.query_p95_ms,
            "context_p50_ms": self.context_p50_ms,
            "context_p95_ms": self.context_p95_ms,
            "ledger_head": self.ledger_head,
            "logical_state_hash": self.logical_state_hash,
            "query_hit_count": self.query_hit_count,
            "budget_pass": self.budget_pass,
            "failed_budgets": list(self.failed_budgets),
        }


def _percentile(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _records(count: int) -> list[dict[str, object]]:
    return [
        {
            "id": f"performance-{index:05d}",
            "text": f"Performance record {index} preserves deterministic ledger semantics",
            "normalized_text": f"Performance record {index} preserves deterministic ledger semantics",
            "category": "general" if index % 2 else "product_decision",
            "status": "accepted" if index % 3 else "verified",
            "source": "system",
            "created_at": f"2026-07-{1 + (index % 9):02d}T12:00:00+00:00",
            "meta": {
                "confidence": 0.8 + ((index % 10) / 100),
                "workspace_realm_id": f"project:performance:{index % 5}",
            },
            "scope": "project",
            "authority": "system_observed",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": index % 4 == 0,
            "can_act_on": False,
        }
        for index in range(count)
    ]


def run_performance_baseline(
    root: Path,
    *,
    record_count: int = 500,
    query_iterations: int = 50,
    context_iterations: int = 20,
    budget: PerformanceBudget = PerformanceBudget(),
) -> PerformanceBaselineResult:
    if record_count < 1 or query_iterations < 1 or context_iterations < 1:
        raise ValueError("record_count and iteration counts must be positive")

    runtime_root = Path(root)
    store = JsonlRecordStore(runtime_root / "ledger")
    sqlite_path = runtime_root / "projections" / "memory.sqlite3"
    search_path = runtime_root / "projections" / "search.json"
    fts_path = runtime_root / "projections" / "fts.json"
    vector_path = runtime_root / "projections" / "vector.json"
    graph_path = runtime_root / "projections" / "graph.json"
    coordinator = ProjectionCoordinator(
        store,
        (
            SQLiteProjectionAdapter(sqlite_path),
            SearchProjectionAdapter(search_path),
            FtsProjectionAdapter(fts_path),
            VectorProjectionAdapter(vector_path),
            GraphProjectionAdapter(graph_path),
        ),
    )

    start = time.perf_counter()
    prepared = prepare_legacy_migration(
        _records(record_count),
        migration_id="mig_performance_baseline_v1",
        recorded_at="2026-07-12T15:00:00+00:00",
    )
    prepare_seconds = time.perf_counter() - start

    start = time.perf_counter()
    applied = LegacyMigrationStagingService(store, coordinator).apply(prepared)
    initial_build_seconds = time.perf_counter() - start

    engine = RetrievalEngine(
        record_store=store,
        projection_coordinator=coordinator,
        search_projection_path=search_path,
    )
    assembler = ContextAssembler(token_budget=1200, max_per_subject_predicate=2)
    request = RetrievalRequest(text="performance deterministic ledger", limit=10)
    warm = engine.retrieve(request)

    query_samples: list[float] = []
    for _ in range(query_iterations):
        started = time.perf_counter()
        engine.retrieve(request)
        query_samples.append((time.perf_counter() - started) * 1000)

    context_samples: list[float] = []
    for _ in range(context_iterations):
        started = time.perf_counter()
        assembler.assemble(engine.retrieve(request))
        context_samples.append((time.perf_counter() - started) * 1000)

    before = coordinator.require_ready_for_query()
    start = time.perf_counter()
    rebuilt = coordinator.rebuild_all()
    rebuild_seconds = time.perf_counter() - start
    after = coordinator.require_ready_for_query()
    if before.logical_state_hash != rebuilt.logical_state_hash or rebuilt.logical_state_hash != after.logical_state_hash:
        raise RuntimeError("performance rebuild changed logical state")

    query_p50 = _percentile(query_samples, 0.50)
    query_p95 = _percentile(query_samples, 0.95)
    context_p50 = _percentile(context_samples, 0.50)
    context_p95 = _percentile(context_samples, 0.95)
    failed: list[str] = []
    if initial_build_seconds > budget.build_seconds_max:
        failed.append("initial_build_seconds")
    if rebuild_seconds > budget.rebuild_seconds_max:
        failed.append("rebuild_seconds")
    if query_p95 > budget.query_p95_ms_max:
        failed.append("query_p95_ms")
    if context_p95 > budget.context_p95_ms_max:
        failed.append("context_p95_ms")

    return PerformanceBaselineResult(
        record_count=record_count,
        query_iterations=query_iterations,
        context_iterations=context_iterations,
        prepare_seconds=round(prepare_seconds, 6),
        initial_build_seconds=round(initial_build_seconds, 6),
        rebuild_seconds=round(rebuild_seconds, 6),
        query_p50_ms=round(query_p50, 3),
        query_p95_ms=round(query_p95, 3),
        context_p50_ms=round(context_p50, 3),
        context_p95_ms=round(context_p95, 3),
        ledger_head=applied.ledger_head,
        logical_state_hash=str(after.logical_state_hash or ""),
        query_hit_count=len(warm.hits),
        budget_pass=not failed,
        failed_budgets=tuple(failed),
    )


__all__ = [
    "PerformanceBaselineResult",
    "PerformanceBudget",
    "run_performance_baseline",
]
