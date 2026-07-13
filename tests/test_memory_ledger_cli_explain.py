from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from ms8 import cli
from ms8.connect.mcp_server import mcp_server
from ms8.connect.mcp_server.stdio_server import TOOL_DEFINITIONS
from ms8.memory.application.legacy_migration import LegacyMigrationStagingService, prepare_legacy_migration
from ms8.memory.application.projection_service import ProjectionCoordinator
from ms8.memory.compat import build_ledger_memory_compatibility_adapter
from ms8.memory.compat import cli as ledger_cli
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

RECORDED_AT = "2026-07-12T11:00:00+00:00"


def _adapter(tmp_path: Path):
    workspace = tmp_path / "workspace"
    ledger_root = workspace / "memory" / "ledger-v1"
    projection_root = workspace / "memory" / "projections"
    manifest_path = workspace / "memory" / "runtime-format.json"
    rows = [
        {
            "id": "explain-visible",
            "text": "Visible explainable preference",
            "normalized_text": "Visible explainable preference",
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
            "id": "explain-hidden",
            "text": "Hidden diagnostic",
            "normalized_text": "Hidden diagnostic",
            "category": "system_diagnostic",
            "status": "accepted",
            "source": "system",
            "created_at": "2026-07-02T04:05:06+00:00",
            "meta": {"confidence": 0.7, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "system_observed",
            "sensitivity": "private",
            "can_recall": False,
            "can_inject": False,
            "can_act_on": False,
        },
    ]
    prepared = prepare_legacy_migration(rows, migration_id="mig_explain_001", recorded_at=RECORDED_AT)
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
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = RuntimeFormatManifest(
        schema=RUNTIME_FORMAT_SCHEMA,
        active_format=LEDGER_V1_RUNTIME_FORMAT,
        generation=1,
        updated_at=RECORDED_AT,
        previous_format=LEGACY_RUNTIME_FORMAT,
        migration_id=prepared.plan.migration_id,
        ledger_head=head,
    )
    manifest_path.write_text(canonical_json(manifest.to_dict()) + "\n", encoding="utf-8")
    adapter = build_ledger_memory_compatibility_adapter(
        {"memory_ledger_v1": {"enabled": True}},
        workspace,
        environ={LEDGER_V1_ENV_FLAG: "1"},
    )
    assert adapter is not None
    claim_ids = tuple(item.claim_id for item in prepared.plan.previews)
    return adapter, claim_ids


def test_explain_returns_trace_and_denies_non_recallable_claim(tmp_path: Path) -> None:
    adapter, claim_ids = _adapter(tmp_path)

    visible = adapter.explain(claim_ids[0])
    hidden = adapter.explain(claim_ids[1])
    missing = adapter.explain("clm_missing")

    assert visible["ok"] is True
    assert visible["provider"] == "ledger-v1"
    assert visible["claim"]["claim_id"] == claim_ids[0]
    assert visible["current_status"] == "verified"
    assert visible["governance"] == {
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    assert visible["evidence"]
    assert visible["decisions"]
    assert visible["source_event"]["event_id"]
    assert hidden == {
        "ok": False,
        "status": "forbidden",
        "reason": "recall_not_allowed",
        "claim_id": claim_ids[1],
    }
    assert missing == {
        "ok": False,
        "status": "not_found",
        "reason": "claim_not_found",
        "claim_id": "clm_missing",
    }


class _FakeAdapter:
    def status(self) -> dict[str, Any]:
        return {"provider": "ledger-v1", "ready_for_query": True}

    def query(self, text: str, limit: int, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "query": text, "count": 0, "results": [], "options": kwargs, "limit": limit}

    def context(self, text: str, limit: int, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "query": text, "context": {}, "options": kwargs, "limit": limit}

    def explain(self, claim_id: str) -> dict[str, Any]:
        return {"ok": True, "claim_id": claim_id}


def test_explicit_ledger_cli_routes_are_json_and_read_only(monkeypatch, capsys, tmp_path: Path) -> None:
    fake = _FakeAdapter()
    monkeypatch.setattr(ledger_cli, "build_ledger_memory_compatibility_adapter", lambda config, workspace: fake)

    status_code = ledger_cli.run_memory_ledger_cli(
        Namespace(workspace=str(tmp_path), memory_ledger_cmd="status")
    )
    status = json.loads(capsys.readouterr().out)
    assert status_code == 0
    assert status == {"ok": True, "provider": "ledger-v1", "ready_for_query": True}

    query_code = ledger_cli.run_memory_ledger_cli(
        Namespace(
            workspace=str(tmp_path),
            memory_ledger_cmd="query",
            text="release",
            limit=7,
            recorded_as_of="2026-07-12T10:00:00+00:00",
            valid_at="",
            realm_id="project:ms8",
            scope="project",
        )
    )
    query = json.loads(capsys.readouterr().out)
    assert query_code == 0
    assert query["query"] == "release"
    assert query["limit"] == 7
    assert query["options"] == {
        "recorded_as_of": "2026-07-12T10:00:00+00:00",
        "observed_as_of": None,
        "valid_at": None,
        "realm_id": "project:ms8",
        "scope": "project",
    }

    explain_code = ledger_cli.run_memory_ledger_cli(
        Namespace(workspace=str(tmp_path), memory_ledger_cmd="explain", claim_id="clm_1")
    )
    explain = json.loads(capsys.readouterr().out)
    assert explain_code == 0
    assert explain == {"ok": True, "claim_id": "clm_1"}


def test_cli_parser_registers_explicit_memory_ledger_commands() -> None:
    parser = cli._build_parser()

    explain = parser.parse_args(
        ["memory-ledger", "--workspace", "/tmp/ms8", "explain", "clm_1"]
    )
    assert explain.command == "memory-ledger"
    assert explain.memory_ledger_cmd == "explain"
    assert explain.workspace == "/tmp/ms8"
    assert explain.claim_id == "clm_1"

    query = parser.parse_args(
        [
            "memory-ledger",
            "--workspace",
            "/tmp/ms8",
            "query",
            "release",
            "--limit",
            "8",
            "--observed-as-of",
            "2026-07-12T10:30:00+00:00",
            "--realm-id",
            "project:ms8",
        ]
    )
    assert query.memory_ledger_cmd == "query"
    assert query.text == "release"
    assert query.limit == 8
    assert query.observed_as_of == "2026-07-12T10:30:00+00:00"
    assert query.realm_id == "project:ms8"


class _ExplainService:
    def ledger_explain(self, claim_id: str) -> dict[str, Any]:
        return {"ok": True, "provider": "ledger-v1", "claim_id": claim_id}


def test_mcp_memory_explain_is_explicit_and_schema_declared(monkeypatch, tmp_path: Path) -> None:
    connect_root = tmp_path / "connect"
    (connect_root / "logs").mkdir(parents=True)
    monkeypatch.setattr(mcp_server, "connect_root", lambda: connect_root)
    monkeypatch.setattr(
        mcp_server.MemoryServiceInterface,
        "from_config",
        classmethod(lambda cls, config: _ExplainService()),
    )

    out = mcp_server.call_tool(
        "memory_explain",
        {"claim_id": "clm_1"},
        config={"mcp": {"enabled": True}},
    )

    assert out == {"ok": True, "provider": "ledger-v1", "claim_id": "clm_1"}
    assert "memory_explain" in mcp_server.TOOL_NAMES
    assert TOOL_DEFINITIONS["memory_explain"]["inputSchema"]["required"] == ["claim_id"]
