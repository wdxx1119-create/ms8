"""Synthetic reference acceptance runner for Hybrid Retrieval v1.

The runner creates an isolated Ledger-v1 workspace from a public fixture, compares
the legacy and hybrid retrieval profiles, and writes deterministic metric structure
plus measured latency. It never reads a user's MS8 workspace.
"""

from __future__ import annotations

import json
import platform
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..application.conflicts import detect_conflicts
from ..application.legacy_migration import LegacyMigrationStagingService, prepare_legacy_migration
from ..application.projection_service import ProjectionCoordinator
from ..application.replay import replay_transactions
from ..compat import build_ledger_memory_compatibility_adapter
from ..domain.ledger import LedgerEvent, LedgerTransaction, canonical_json
from ..domain.models import Actor
from ..infrastructure.fts_projection import FtsProjectionAdapter
from ..infrastructure.graph_projection import GraphProjectionAdapter
from ..infrastructure.jsonl_ledger import JsonlRecordStore
from ..infrastructure.search_projection import SearchProjectionAdapter
from ..infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter
from ..infrastructure.vector_projection import VectorProjectionAdapter
from ..runtime_format import (
    LEDGER_V1_ENV_FLAG,
    LEDGER_V1_RUNTIME_FORMAT,
    LEGACY_RUNTIME_FORMAT,
    RUNTIME_FORMAT_SCHEMA,
    RuntimeFormatManifest,
)
from .evaluation import EvaluationCase, EvaluationObservation, compare_profiles
from .runtime import HYBRID_RETRIEVAL_ENV_FLAG, HYBRID_RETRIEVAL_PROFILE

REFERENCE_REPORT_SCHEMA = "ms8.hybrid_reference_acceptance.v1"


@dataclass(frozen=True, slots=True)
class ReferenceAcceptanceArtifacts:
    report_json: Path
    report_markdown: Path
    report: Mapping[str, Any]


def _load_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("evaluation fixture must be an object")
    if payload.get("schema") != "ms8.hybrid_evaluation_fixture.v1":
        raise ValueError("evaluation fixture schema mismatch")
    claims = payload.get("claims")
    queries = payload.get("queries")
    if not isinstance(claims, list) or not claims:
        raise ValueError("evaluation fixture must contain claims")
    if not isinstance(queries, list) or not queries:
        raise ValueError("evaluation fixture must contain queries")
    return payload


def _projection_adapters(root: Path) -> tuple[Any, ...]:
    return (
        SQLiteProjectionAdapter(root / "memory.sqlite3"),
        SearchProjectionAdapter(root / "search.json"),
        FtsProjectionAdapter(root / "fts.json"),
        VectorProjectionAdapter(root / "vector.json"),
        GraphProjectionAdapter(root / "graph.json"),
    )


def _append_detected_conflicts(store: JsonlRecordStore, recorded_at: str) -> int:
    state = replay_transactions(store.iterate())
    conflicts = detect_conflicts(state)
    if not conflicts:
        return 0
    transaction = LedgerTransaction.create(
        sequence=state.last_sequence + 1,
        prev_hash=state.ledger_head,
        actor=Actor(kind="system", id="hybrid-reference-acceptance"),
        transaction_id="txn_hybrid_reference_conflicts_v1",
        recorded_at=recorded_at,
        events=tuple(
            LedgerEvent(
                type="conflict.detected",
                payload=conflict.to_event_payload(detected_at=recorded_at),
            )
            for conflict in conflicts
        ),
    )
    store.append(transaction, expected_head=state.ledger_head)
    return len(conflicts)


def _build_workspace(
    fixture: Mapping[str, Any],
    workspace: Path,
) -> tuple[dict[str, str], int]:
    raw_claims = fixture["claims"]
    assert isinstance(raw_claims, list)
    rows: list[Mapping[str, Any]] = []
    keys: list[str] = []
    for item in raw_claims:
        if not isinstance(item, Mapping):
            raise TypeError("fixture claims must contain objects")
        key = str(item.get("key") or "").strip()
        record = item.get("record")
        if not key or not isinstance(record, Mapping):
            raise ValueError("each fixture claim requires key and record")
        keys.append(key)
        rows.append(record)
    if len(set(keys)) != len(keys):
        raise ValueError("fixture claim keys must be unique")

    recorded_at = str(fixture.get("recorded_at") or "").strip()
    prepared = prepare_legacy_migration(
        rows,
        migration_id="mig_hybrid_reference_acceptance_v1",
        recorded_at=recorded_at,
    )
    ledger_root = workspace / "memory" / "ledger-v1"
    projection_root = workspace / "memory" / "projections"
    store = JsonlRecordStore(ledger_root)
    LegacyMigrationStagingService(store).apply(prepared)
    key_to_claim = {
        key: preview.claim_id for key, preview in zip(keys, prepared.plan.previews, strict=True)
    }
    conflict_count = _append_detected_conflicts(store, recorded_at)
    ProjectionCoordinator(store, _projection_adapters(projection_root)).rebuild_all()

    verification = store.verify()
    if not verification.valid or verification.last_valid_hash is None:
        raise RuntimeError("synthetic acceptance ledger verification failed")
    manifest = RuntimeFormatManifest(
        schema=RUNTIME_FORMAT_SCHEMA,
        active_format=LEDGER_V1_RUNTIME_FORMAT,
        generation=1,
        updated_at=recorded_at,
        previous_format=LEGACY_RUNTIME_FORMAT,
        migration_id=prepared.plan.migration_id,
        ledger_head=verification.last_valid_hash,
    )
    manifest_path = workspace / "memory" / "runtime-format.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(canonical_json(manifest.to_dict()) + "\n", encoding="utf-8")
    return key_to_claim, conflict_count


def _adapter(workspace: Path, profile: str):
    section: dict[str, Any] = {
        "enabled": True,
        "retrieval_profile": profile,
        "context_token_budget": 900,
        "max_per_subject_predicate": 3,
    }
    if profile == HYBRID_RETRIEVAL_PROFILE:
        section["hybrid"] = {
            "principal_realm_ids": ["project:demo"],
            "principal_scopes": ["project"],
            "max_claims": 20,
            "max_per_subject": 5,
            "max_per_predicate": 5,
            "max_per_subject_predicate": 3,
            "graph_max_hops": 2,
        }
    environment = {LEDGER_V1_ENV_FLAG: "1"}
    if profile == HYBRID_RETRIEVAL_PROFILE:
        environment[HYBRID_RETRIEVAL_ENV_FLAG] = "1"
    adapter = build_ledger_memory_compatibility_adapter(
        {"memory_ledger_v1": section},
        workspace,
        environ=environment,
    )
    if adapter is None:
        raise RuntimeError(f"failed to construct {profile} acceptance adapter")
    return adapter


def _case_from_fixture(raw: Mapping[str, Any], key_to_claim: Mapping[str, str]) -> EvaluationCase:
    mapped = dict(raw)
    relevance = raw.get("relevance", {})
    if not isinstance(relevance, Mapping):
        raise TypeError("fixture query relevance must be an object")
    mapped["relevance"] = {
        key_to_claim[str(key)]: value for key, value in relevance.items()
    }
    for field_name in (
        "expected_current",
        "expected_historical",
        "expected_conflicts",
        "forbidden_claims",
    ):
        values = raw.get(field_name, ())
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            raise TypeError(f"fixture query {field_name} must be an array")
        mapped[field_name] = [key_to_claim[str(value)] for value in values]
    return EvaluationCase.from_mapping(mapped)


def _observe(
    adapter: Any,
    raw_case: Mapping[str, Any],
    case: EvaluationCase,
) -> EvaluationObservation:
    started = time.perf_counter()
    result = adapter.query(
        case.text,
        20,
        purpose=case.purpose,
        explain=True,
        realm_id=str(raw_case.get("realm_id") or "") or None,
        scope=str(raw_case.get("scope") or "") or None,
    )
    latency_ms = (time.perf_counter() - started) * 1000.0
    if result.get("ok") is not True:
        raise RuntimeError(f"acceptance query failed: {case.case_id}")
    rows = result.get("results", ())
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise TypeError("acceptance query results must be an array")

    ranked: list[str] = []
    traceable: list[str] = []
    conflicts: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("acceptance result rows must be objects")
        claim_id = str(row.get("id") or "").strip()
        if not claim_id:
            continue
        ranked.append(claim_id)
        provenance = row.get("provenance")
        if isinstance(provenance, Mapping):
            evidence_ids = provenance.get("evidence_ids")
            decision_ids = provenance.get("decision_ids")
            if evidence_ids and decision_ids:
                traceable.append(claim_id)
        row_conflicts = row.get("conflicts")
        if isinstance(row_conflicts, Sequence) and not isinstance(
            row_conflicts, (str, bytes, bytearray)
        ) and row_conflicts:
            conflicts.append(claim_id)

    degraded: list[str] = []
    ledger = result.get("ledger_v1")
    hybrid = ledger.get("hybrid") if isinstance(ledger, Mapping) else None
    sources = hybrid.get("sources") if isinstance(hybrid, Mapping) else None
    if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes, bytearray)):
        for source in sources:
            if isinstance(source, Mapping) and source.get("status") == "degraded":
                name = str(source.get("source") or "").strip()
                if name:
                    degraded.append(name)

    return EvaluationObservation(
        case_id=case.case_id,
        ranked_claim_ids=tuple(ranked),
        traceable_claim_ids=tuple(traceable),
        presented_conflict_claim_ids=tuple(conflicts),
        degraded_sources=tuple(degraded),
        latency_ms=latency_ms,
    )


def _release_gates(report: Mapping[str, Any], platform_name: str) -> dict[str, bool]:
    baseline = report["baseline"]
    hybrid = report["hybrid"]
    comparison = report["comparison"]
    assert isinstance(baseline, Mapping)
    assert isinstance(hybrid, Mapping)
    assert isinstance(comparison, Mapping)
    baseline_metrics = baseline["metrics"]
    hybrid_metrics = hybrid["metrics"]
    assert isinstance(baseline_metrics, Mapping)
    assert isinstance(hybrid_metrics, Mapping)
    baseline_slices = baseline.get("slices", {})
    hybrid_slices = hybrid.get("slices", {})
    assert isinstance(baseline_slices, Mapping)
    assert isinstance(hybrid_slices, Mapping)

    critical_slices = ("english", "historical", "conflict", "code")
    critical_ok = True
    for name in critical_slices:
        baseline_slice = baseline_slices.get(name, {})
        hybrid_slice = hybrid_slices.get(name, {})
        if not isinstance(baseline_slice, Mapping) or not isinstance(hybrid_slice, Mapping):
            critical_ok = False
            continue
        if float(hybrid_slice.get("ndcg_at_10", 0.0)) < float(
            baseline_slice.get("ndcg_at_10", 0.0)
        ):
            critical_ok = False

    return {
        "platform_is_macos": platform_name == "Darwin",
        "unauthorized_inactive_error_recall_is_zero": float(
            hybrid_metrics["unauthorized_inactive_error_recall_rate"]
        )
        == 0.0,
        "evidence_and_decision_coverage_is_complete": float(
            hybrid_metrics["evidence_citation_coverage"]
        )
        == 1.0,
        "degradation_is_correct": float(hybrid_metrics["degradation_correctness"]) == 1.0,
        "ndcg_at_10_relative_improvement_at_least_5_percent": float(
            comparison["ndcg_at_10_relative_improvement"]
        )
        >= 0.05,
        "recall_at_20_has_no_regression": float(comparison["recall_at_20_delta"]) >= 0.0,
        "critical_slices_have_no_regression": critical_ok,
    }


def _markdown(report: Mapping[str, Any]) -> str:
    hybrid = report["comparison"]["hybrid"]
    baseline = report["comparison"]["baseline"]
    comparison = report["comparison"]["comparison"]
    gates = report["release_gates"]
    lines = [
        "# Hybrid Retrieval v1 reference acceptance",
        "",
        f"- Platform: `{report['platform']}`",
        f"- Fixture: `{report['fixture_schema']}`",
        f"- Conflict records: `{report['conflict_count']}`",
        "",
        "## Metrics",
        "",
        "| Metric | Legacy | Hybrid |",
        "|---|---:|---:|",
    ]
    for key in (
        "ndcg_at_5",
        "ndcg_at_10",
        "mrr",
        "recall_at_20",
        "current_fact_accuracy",
        "historical_fact_accuracy",
        "evidence_citation_coverage",
        "conflict_presentation_rate",
        "unauthorized_inactive_error_recall_rate",
        "degradation_correctness",
        "latency_ms_p50",
        "latency_ms_p95",
    ):
        lines.append(
            f"| `{key}` | {float(baseline['metrics'][key]):.6f} | "
            f"{float(hybrid['metrics'][key]):.6f} |"
        )
    lines.extend(
        [
            "",
            "## Comparison",
            "",
            f"- nDCG@10 relative improvement: {float(comparison['ndcg_at_10_relative_improvement']):.6f}",
            f"- Recall@20 delta: {float(comparison['recall_at_20_delta']):.6f}",
            "",
            "## Release gates",
            "",
        ]
    )
    lines.extend(f"- [{'x' if passed else ' '}] `{name}`" for name, passed in gates.items())
    lines.append("")
    return "\n".join(lines)


def run_reference_acceptance(
    fixture_path: Path,
    output_dir: Path,
    *,
    workspace: Path,
    platform_name: str | None = None,
) -> ReferenceAcceptanceArtifacts:
    fixture = _load_fixture(fixture_path)
    workspace = Path(workspace)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    key_to_claim, conflict_count = _build_workspace(fixture, workspace)
    raw_queries = fixture["queries"]
    assert isinstance(raw_queries, list)
    cases = tuple(
        _case_from_fixture(raw, key_to_claim)
        for raw in raw_queries
        if isinstance(raw, Mapping)
    )
    if len(cases) != len(raw_queries):
        raise TypeError("fixture queries must contain objects")

    legacy = _adapter(workspace, "legacy")
    hybrid = _adapter(workspace, HYBRID_RETRIEVAL_PROFILE)
    baseline_observations = tuple(
        _observe(legacy, raw, case)
        for raw, case in zip(raw_queries, cases, strict=True)
        if isinstance(raw, Mapping)
    )
    hybrid_observations = tuple(
        _observe(hybrid, raw, case)
        for raw, case in zip(raw_queries, cases, strict=True)
        if isinstance(raw, Mapping)
    )
    comparison = compare_profiles(cases, baseline_observations, hybrid_observations)
    resolved_platform = platform_name or platform.system()
    gates = _release_gates(comparison, resolved_platform)
    report: dict[str, Any] = {
        "schema": REFERENCE_REPORT_SCHEMA,
        "fixture_schema": fixture["schema"],
        "platform": resolved_platform,
        "conflict_count": conflict_count,
        "comparison": comparison,
        "release_gates": gates,
        "accepted": all(gates.values()),
        "golden_ordering": {
            observation.case_id: list(observation.ranked_claim_ids)
            for observation in hybrid_observations
        },
    }
    json_path = output_dir / "reference_acceptance.json"
    markdown_path = output_dir / "reference_acceptance.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return ReferenceAcceptanceArtifacts(
        report_json=json_path,
        report_markdown=markdown_path,
        report=report,
    )


__all__ = [
    "REFERENCE_REPORT_SCHEMA",
    "ReferenceAcceptanceArtifacts",
    "run_reference_acceptance",
]
