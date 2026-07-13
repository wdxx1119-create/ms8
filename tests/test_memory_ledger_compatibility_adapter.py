from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ms8.connect.mcp_server import mcp_server
from ms8.connect.mcp_server.memory_service_interface import MemoryServiceInterface
from ms8.connect.mcp_server.stdio_server import TOOL_DEFINITIONS
from ms8.memory.application.legacy_migration import LegacyMigrationStagingService, prepare_legacy_migration
from ms8.memory.application.projection_service import ProjectionCoordinator
from ms8.memory.compat import (
    LedgerCompatibilityError,
    LedgerMemoryCompatibilityAdapter,
    build_ledger_memory_compatibility_adapter,
)
from ms8.memory.domain.ledger import canonical_json
from ms8.memory.infrastructure.fts_projection import FtsProjectionAdapter
from ms8.memory.infrastructure.graph_projection import GraphProjectionAdapter
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter
from ms8.memory.infrastructure.vector_projection import VectorProjectionAdapter
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
            "id": "compat-1",
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
            "id": "compat-2",
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


def _runtime(tmp_path: Path) -> tuple[Path, dict[str, Any], LedgerMemoryCompatibilityAdapter]:
    workspace = tmp_path / "workspace"
    ledger_root = workspace / "memory" / "ledger-v1"
    projection_root = workspace / "memory" / "projections"
    runtime_manifest = workspace / "memory" / "runtime-format.json"

    prepared = prepare_legacy_migration(
        _rows(),
        migration_id="mig_compat_001",
        recorded_at=RECORDED_AT,
    )
    store = JsonlRecordStore(ledger_root)
    LegacyMigrationStagingService(store).apply(prepared)
    coordinator = ProjectionCoordinator(
        store,
        (
            SQLiteProjectionAdapter(projection_root / "memory.sqlite3"),
            SearchProjectionAdapter(projection_root / "search.json"),
            FtsProjectionAdapter(projection_root / "fts.json"),
            VectorProjectionAdapter(projection_root / "vector.json"),
            GraphProjectionAdapter(projection_root / "graph.json"),
        ),
    )
    coordinator.rebuild_all()
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

    config: dict[str, Any] = {
        "memory_ledger_v1": {
            "enabled": True,
            "context_token_budget": 400,
        }
    }
    adapter = build_ledger_memory_compatibility_adapter(
        config,
        workspace,
        environ={LEDGER_V1_ENV_FLAG: "1"},
    )
    assert adapter is not None
    return workspace, config, adapter


def test_builder_requires_config_manifest_and_environment_gates(tmp_path: Path) -> None:
    workspace, config, adapter = _runtime(tmp_path)
    assert adapter.status()["ready_for_query"] is True

    assert build_ledger_memory_compatibility_adapter({}, workspace) is None
    with pytest.raises(LedgerCompatibilityError, match="flag_required"):
        build_ledger_memory_compatibility_adapter(config, workspace, environ={})


def test_adapter_preserves_query_and_context_primary_fields(tmp_path: Path) -> None:
    _, _, adapter = _runtime(tmp_path)

    query = adapter.query(
        "concise release",
        5,
        realm_id="project:ms8",
        scope="project",
    )
    assert query["ok"] is True
    assert query["query"] == "concise release"
    assert query["count"] == 1
    assert query["results"][0]["id"]
    assert query["results"][0]["can_recall"] is True
    assert query["results"][0]["provenance"]["evidence_ids"]
    assert query["retrieval_gateway"]["provider"] == "ledger-v1"
    assert query["retrieval_gateway"]["policy_filter"]["blocked_reasons"] == {
        "recall_not_allowed": 1
    }

    context = adapter.context("concise release", 5, realm_id="project:ms8")
    assert context["ok"] is True
    assert context["query"] == "concise release"
    assert context["context"]["context"]
    assert context["context"]["memories"][0]["can_inject"] is True
    assert context["retrieval_gateway"]["provider"] == "ledger-v1"
    assert context["system_prompt_extra"] == ""
    assert context["context_with_expression"] == context["context"]["context"]


def test_memory_service_uses_explicit_ledger_adapter_and_rejects_legacy_write(tmp_path: Path) -> None:
    _, _, adapter = _runtime(tmp_path)
    service = MemoryServiceInterface(
        config={},
        core=None,
        ledger_adapter=adapter,
        ledger_requested=True,
    )

    query = service.query("concise", 3, realm_id="project:ms8")
    context = service.context("concise", 3, realm_id="project:ms8")
    submit = service.submit({"content": "must not enter legacy authority"})

    assert query["ok"] is True
    assert query["retrieval_gateway"]["provider"] == "ledger-v1"
    assert context["ok"] is True
    assert context["recommended_actions"]
    assert submit == {
        "ok": False,
        "accepted": False,
        "error": "ledger_v1_write_not_enabled",
        "error_code": "E_LEDGER_V1_WRITE_NOT_ENABLED",
    }


class _ExtendedMemoryService:
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


def test_mcp_forwards_optional_temporal_and_scope_fields(monkeypatch, tmp_path: Path) -> None:
    service = _ExtendedMemoryService()
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
            "text": "release",
            "top_k": 7,
            "recorded_as_of": "2026-07-12T09:00:00+00:00",
            "observed_as_of": "2026-07-12T08:30:00+00:00",
            "valid_at": "2026-07-12T08:00:00+00:00",
            "realm_id": "project:ms8",
            "scope": "project",
        },
        config={"mcp": {"enabled": True}},
    )
    prepared = mcp_server.call_tool(
        "prepare_reply",
        {
            "text": "release",
            "limit": 4,
            "recorded_as_of": "2026-07-12T09:00:00+00:00",
            "observed_as_of": "2026-07-12T08:30:00+00:00",
            "realm_id": "project:ms8",
        },
        config={"mcp": {"enabled": True}},
    )

    assert query["ok"] is True
    assert prepared["must_call_before_answer"] is True
    assert service.calls == [
        (
            "query",
            "release",
            7,
            {
                "recorded_as_of": "2026-07-12T09:00:00+00:00",
                "observed_as_of": "2026-07-12T08:30:00+00:00",
                "valid_at": "2026-07-12T08:00:00+00:00",
                "realm_id": "project:ms8",
                "scope": "project",
            },
        ),
        (
            "context",
            "release",
            4,
            {
                "recorded_as_of": "2026-07-12T09:00:00+00:00",
                "observed_as_of": "2026-07-12T08:30:00+00:00",
                "realm_id": "project:ms8",
            },
        ),
    ]

    optional_fields = {"recorded_as_of", "observed_as_of", "valid_at", "realm_id", "scope"}
    assert optional_fields.issubset(TOOL_DEFINITIONS["query"]["inputSchema"]["properties"])
    assert optional_fields.issubset(TOOL_DEFINITIONS["context"]["inputSchema"]["properties"])
    assert optional_fields.issubset(TOOL_DEFINITIONS["prepare_reply"]["inputSchema"]["properties"])
