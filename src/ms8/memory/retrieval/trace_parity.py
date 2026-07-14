"""Deterministic cross-platform fingerprints for Hybrid Retrieval v1 traces.

Fingerprints cover query plans, eligibility, source hits, fusion, reranking, selected
claims, scores, tie-break explanations, and policy/degradation traces. Runtime
latencies and filesystem paths are intentionally excluded.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .reference_acceptance import (
    _adapter,
    _build_workspace,
    _case_from_fixture,
    _load_fixture,
)
from .runtime import HYBRID_RETRIEVAL_PROFILE

TRACE_PARITY_SCHEMA = "ms8.hybrid_trace_parity.v1"


def _normalize(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 12)
    if isinstance(value, Mapping):
        return {
            str(key): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    return value


def _row_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _normalize(row.get(key))
        for key in (
            "id",
            "score",
            "status",
            "scope",
            "realm_id",
            "authority",
            "sensitivity",
            "can_recall",
            "can_inject",
            "can_act_on",
            "provenance",
            "conflicts",
            "ranking_explanation",
            "matched_terms",
        )
    }


def _snapshot(result: Mapping[str, Any]) -> dict[str, Any]:
    ledger = result.get("ledger_v1")
    hybrid = ledger.get("hybrid") if isinstance(ledger, Mapping) else None
    if not isinstance(hybrid, Mapping):
        raise TypeError("hybrid explain trace is missing")
    rows = result.get("results", ())
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise TypeError("hybrid result rows must be an array")
    gateway = result.get("retrieval_gateway")
    if not isinstance(gateway, Mapping):
        raise TypeError("retrieval gateway trace is missing")
    return _normalize(
        {
            "query": result.get("query"),
            "count": result.get("count"),
            "plan": hybrid.get("plan"),
            "eligibility": hybrid.get("eligibility"),
            "sources": hybrid.get("sources"),
            "source_hits": hybrid.get("source_hits"),
            "fusion": hybrid.get("fusion"),
            "reranking": hybrid.get("reranking"),
            "results": [
                _row_snapshot(row)
                for row in rows
                if isinstance(row, Mapping)
            ],
            "retrieval_gateway": dict(gateway),
        }
    )


def _fingerprint(snapshot: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capture_trace_parity(
    fixture_path: Path,
    *,
    workspace: Path,
) -> dict[str, Any]:
    """Build the public fixture and capture deterministic full-trace fingerprints."""

    fixture = _load_fixture(fixture_path)
    key_to_claim, _ = _build_workspace(fixture, Path(workspace))
    raw_queries = fixture["queries"]
    if not isinstance(raw_queries, list):
        raise TypeError("fixture queries must be an array")
    cases = tuple(
        _case_from_fixture(raw, key_to_claim)
        for raw in raw_queries
        if isinstance(raw, Mapping)
    )
    if len(cases) != len(raw_queries):
        raise TypeError("fixture queries must contain objects")
    adapter = _adapter(Path(workspace), HYBRID_RETRIEVAL_PROFILE)

    snapshots: dict[str, Any] = {}
    fingerprints: dict[str, str] = {}
    for raw, case in zip(raw_queries, cases, strict=True):
        if not isinstance(raw, Mapping):
            raise TypeError("fixture query must be an object")
        result = adapter.query(
            case.text,
            20,
            purpose=case.purpose,
            explain=True,
            realm_id=str(raw.get("realm_id") or "") or None,
            scope=str(raw.get("scope") or "") or None,
        )
        if result.get("ok") is not True:
            raise RuntimeError(f"trace parity query failed: {case.case_id}")
        snapshot = _snapshot(result)
        snapshots[case.case_id] = snapshot
        fingerprints[case.case_id] = _fingerprint(snapshot)

    return {
        "schema": TRACE_PARITY_SCHEMA,
        "fixture_schema": fixture.get("schema"),
        "fingerprints": dict(sorted(fingerprints.items())),
        "snapshots": dict(sorted(snapshots.items())),
    }


def write_trace_parity_report(
    fixture_path: Path,
    output_path: Path,
    *,
    workspace: Path,
) -> dict[str, Any]:
    report = capture_trace_parity(fixture_path, workspace=workspace)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = [
    "TRACE_PARITY_SCHEMA",
    "capture_trace_parity",
    "write_trace_parity_report",
]
