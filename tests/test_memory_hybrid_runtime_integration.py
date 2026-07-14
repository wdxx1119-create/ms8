from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ms8.cli import _build_parser
from ms8.connect.mcp_server import mcp_server
from ms8.connect.mcp_server.memory_service_interface import MemoryServiceInterface
from ms8.connect.mcp_server.stdio_server import TOOL_DEFINITIONS
from ms8.memory.application.legacy_migration import LegacyMigrationStagingService, prepare_legacy_migration
from ms8.memory.application.projection_service import ProjectionCoordinator
from ms8.memory.compat import LedgerCompatibilityError, build_ledger_memory_compatibility_adapter
from ms8.memory.domain.ledger import canonical_json
from ms8.memory.infrastructure.fts_projection import FtsProjectionAdapter
from ms8.memory.infrastructure.graph_projection import GraphProjectionAdapter
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter
from ms8.memory.infrastructure.vector_projection import VectorProjectionAdapter
from ms8.memory.retrieval import HYBRID_RETRIEVAL_ENV_FLAG, HYBRID_RETRIEVAL_PROFILE
from ms8.memory.runtime_format import (
    LEDGER_V1_ENV_FLAG,
    LEDGER_V1_RUNTIME_FORMAT,
    LEGACY_RUNTIME_FORMAT,
    RUNTIME_FORMAT_SCHEMA,
    RuntimeFormatManifest,
)

RECORDED_AT = "2026-07-12T10:00:00+00:00"


def _rows() -> list[dict[str, object]]:
    return [
        {
            "id": "hybrid-compat-1",
            "text": "User prefers concise release summaries",
            "normalized_text": "User prefers concise release summaries",
            "category": "user_preference",
            "status": "verified",
            "source": "ask",
            "created_at": "2026-07-01T01:02:03+00:00",
            "meta": {"confidence": 0.95, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "user_explicit",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
            "can_act_on": False,
        },
        {
            "id": "hybrid-compat-2",
            "text": "Hidden release diagnostic",
            "normalized_text": "Hidden release diagnostic",
            "category": "system_diagnostic",
            "status": "accepted",
            "source": "system",
            "created_at": "2026-07-02T04:05:06+00:00",
            "meta": {"confidence": 0.8, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "system_observed",
            "sensitivity": "private",
            "can_recall": False,
            "can_inject": False,
            "can_act_on": False,
        },
    ]


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    ledger_root = workspace / "memory" / "ledger-v1"
    projection_root = workspace / "memory" / "projections"
    runtime_manifest = workspace / "memory" / "runtime-format.json"

    prepared = prepare_legacy_migration(
        _rows(),
        migration_id="mig_hybrid_compat_001",
        recorded_at=RECORDED_AT,
    )
    store = JsonlRecordStore(ledger_root)
    LegacyMigrationStagingService(store).apply(prepared)
    ProjectionCoordinator(
        store,
        (
            SQLiteProjectionAdapter(projection_root / "memory.sqlite3"),
            SearchProjectionAdapter(projection_root / "search.json"),
            FtsProjectionAdapter(projection_root / "fts.json"),
            VectorProjectionAdapter(projection_root / "vector.json"),
            GraphProjectionAdapter(projection_root / "graph.json"),
        ),
    ).rebuild_all()
    head = store.verify().last_valid_hash
    assert head is not None

    runtime_manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest = RuntimeFormatManifest(
        schema=RUNTIME_FORMAT_SCHEMA,
        active_format=LEDGER_V1_RUNTIME_FORMAT,
        generation=1,
        updated_at=RECORDED_AT,
        previous_format=LEGACY_RUNTIME_FORMAT,
        migration_id=prepared.plan.migration_id,
        ledger_head=head,
    )
    runtime_manifest.write_text(canonical_json(manifest.to_dict()) + "\n", encoding="utf-8")
    return workspace


def _config(profile: str = HYBRID_RETRIEVAL_PROFILE) -> dict[str, Any]:
    return {
        "memory_ledger_v1": {
            "enabled": True,
            "retrieval_profile": profile,
            "context_token_budget": 500,
            "hybrid": {
                "max_claims": 5,
                "max_per_subject": 3,
                "max_per_predicate": 3,
            },
        }
    }


def _hybrid_adapter(tmp_path: Path):
    workspace = _workspace(tmp_path)
    adapter = build_ledger_memory_compatibility_adapter(
        _config(),
        workspace,
        environ={LEDGER_V1_ENV_FLAG: "1", HYBRID_RETRIEVAL_ENV_FLAG: "1"},
    )
    assert adapter is not None
    return workspace, adapter


def test_hybrid_profile_requires_its_explicit_environment_gate(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(LedgerCompatibilityError, match=HYBRID_RETRIEVAL_ENV_FLAG):
        build_ledger_memory_compatibility_adapter(
            _config(),
            workspace,
            environ={LEDGER_V1_ENV_FLAG: "1"},
        )

    legacy = build_ledger_memory_compatibility_adapter(
        _config("legacy"),
        workspace,
        environ={LEDGER_V1_ENV_FLAG: "1"},
    )
    assert legacy is not None
    assert legacy.status()["retrieval_profile"] == "legacy"
    assert legacy.status()["hybrid_ready"] is False


def test_hybrid_query_exposes_full_governed_trace_and_degrades_vector_only(tmp_path: Path) -> None:
    _, adapter = _hybrid_adapter(tmp_path)

    result = adapter.query(
        "concise release",
        5,
        explain=True,
        realm_id="project:ms8",
        scope="project",
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["results"][0]["can_recall"] is True
    assert result["retrieval_gateway"]["candidate_source"] == HYBRID_RETRIEVAL_PROFILE
    assert result["retrieval_gateway"]["retrieval_profile"] == HYBRID_RETRIEVAL_PROFILE
    assert result["retrieval_gateway"]["manifest_generation"] == 1
    trace = result["ledger_v1"]["hybrid"]
    assert trace["profile"] == HYBRID_RETRIEVAL_PROFILE
    assert trace["plan"]["plan"]["query"]["text"] == "concise release"
    assert trace["eligibility"]["eligible_count"] == 1
    assert trace["source_hits"]
    assert trace["fusion"]["config_schema"] == "ms8.hybrid_fusion.v1"
    assert trace["reranking"]["ranked"][0]["claim_id"] == result["results"][0]["id"]
    vector = next(item for item in trace["sources"] if item["channel"] == "vector")
    assert vector["status"] == "degraded"
    assert vector["degradation_reason"] == "embedding-unavailable:RuntimeError"


def test_hybrid_context_is_budgeted_traceable_and_policy_bounded(tmp_path: Path) -> None:
    _, adapter = _hybrid_adapter(tmp_path)

    result = adapter.context(
        "concise release",
        5,
        explain=True,
        realm_id="project:ms8",
        scope="project",
    )

    assert result["ok"] is True
    assert result["context"]["memories"]
    assert "[MS8_POLICY_BOUNDARY schema=ms8.agent_context.v1]" in result["context"]["context"]
    assert result["context"]["citations"]
    assert result["context"]["retrieval_gateway"]["retrieval_profile"] == HYBRID_RETRIEVAL_PROFILE
    assembly = result["ledger_v1"]["hybrid"]["assembly"]
    assert assembly["estimated_tokens"] <= assembly["budget_tokens"]
    assert assembly["selected_claim_ids"]
    assert assembly["evidence_ids"]
    assert assembly["decision_ids"]


def test_cli_parser_exposes_profile_purpose_and_explain() -> None:
    args = _build_parser().parse_args(
        [
            "memory-ledger",
            "--workspace",
            "/tmp/ms8",
            "--retrieval-profile",
            "hybrid-v1",
            "query",
            "old release rule",
            "--purpose",
            "historical",
            "--explain",
        ]
    )

    assert args.retrieval_profile == "hybrid-v1"
    assert args.purpose == "historical"
    assert args.explain is True


class _ForwardingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, dict[str, Any]]] = []

    def query(self, text: str, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("query", text, top_k, kwargs))
        return {"ok": True, "query": text, "count": 0, "results": []}

    def context(self, text: str, limit: int = 5, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("context", text, limit, kwargs))
        return {
            "ok": True,
            "query": text,
            "context": {},
            "system_prompt_extra": "",
            "recommended_actions": [],
        }


def test_mcp_forwards_hybrid_purpose_explain_and_observed_time(monkeypatch, tmp_path: Path) -> None:
    service = _ForwardingService()
    connect_root = tmp_path / "connect"
    (connect_root / "logs").mkdir(parents=True)
    monkeypatch.setattr(mcp_server, "connect_root", lambda: connect_root)
    monkeypatch.setattr(
        mcp_server.MemoryServiceInterface,
        "from_config",
        classmethod(lambda cls, config: service),
    )

    query = mcp_server.call_tool(
        "query",
        {
            "text": "old release rule",
            "top_k": 7,
            "purpose": "historical",
            "explain": True,
            "observed_as_of": "2026-07-12T08:30:00+00:00",
        },
        config={"mcp": {"enabled": True}},
    )
    prepared = mcp_server.call_tool(
        "prepare_reply",
        {
            "text": "release",
            "limit": 4,
            "explain": True,
            "observed_as_of": "2026-07-12T08:30:00+00:00",
        },
        config={"mcp": {"enabled": True}},
    )

    assert query["ok"] is True
    assert prepared["must_call_before_answer"] is True
    assert service.calls == [
        (
            "query",
            "old release rule",
            7,
            {
                "observed_as_of": "2026-07-12T08:30:00+00:00",
                "explain": True,
                "purpose": "historical",
            },
        ),
        (
            "context",
            "release",
            4,
            {
                "observed_as_of": "2026-07-12T08:30:00+00:00",
                "explain": True,
            },
        ),
    ]
    assert "purpose" in TOOL_DEFINITIONS["query"]["inputSchema"]["properties"]
    for tool in ("query", "context", "prepare_reply"):
        assert "explain" in TOOL_DEFINITIONS[tool]["inputSchema"]["properties"]


def test_memory_service_delegates_hybrid_fields_without_changing_primary_shape(tmp_path: Path) -> None:
    _, adapter = _hybrid_adapter(tmp_path)
    service = MemoryServiceInterface(
        config={},
        core=None,
        ledger_adapter=adapter,
        ledger_requested=True,
    )

    query = service.query(
        "concise release",
        3,
        purpose="recall",
        explain=True,
        observed_as_of="2026-07-12T09:00:00+00:00",
        realm_id="project:ms8",
        scope="project",
    )
    context = service.context(
        "concise release",
        3,
        explain=True,
        observed_as_of="2026-07-12T09:00:00+00:00",
        realm_id="project:ms8",
        scope="project",
    )

    assert query["ok"] is True
    assert set(("query", "count", "results", "retrieval_gateway")).issubset(query)
    assert context["ok"] is True
    assert set(("query", "context", "system_prompt_extra", "recommended_actions")).issubset(context)
