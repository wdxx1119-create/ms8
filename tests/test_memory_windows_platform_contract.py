from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

import pytest

from ms8.memory.application.projection_recovery import ProjectionRecoveryService
from ms8.memory.application.projection_service import ProjectionCoordinator
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, Decision, Evidence, MemoryEvent, ValidTime
from ms8.memory.infrastructure.fts_projection import FtsProjectionAdapter
from ms8.memory.infrastructure.graph_projection import GraphProjectionAdapter
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter
from ms8.memory.infrastructure.vector_projection import VectorProjectionAdapter

RECORDED_AT = "2026-07-12T18:00:00+00:00"


def _transaction() -> LedgerTransaction:
    event = MemoryEvent(
        event_id="evt_windows_path_001",
        kind="user_input",
        content={"text": "Windows Unicode workspace keeps ledger projections durable"},
        source={"system": "windows-acceptance", "path": "输入 记录.jsonl"},
        observed_at="2026-07-12T17:55:00+00:00",
        trust_class="user_explicit",
    )
    claim = Claim(
        claim_id="clm_windows_path_001",
        kind="decision",
        text="Windows Unicode workspace keeps ledger projections durable",
        subject="project:ms8",
        predicate="windows_storage_contract",
        value="durable",
        scope="project",
        realm_id="project:ms8",
        authority="user_explicit",
        sensitivity="internal",
        confidence=0.99,
        status="proposed",
        valid_time=ValidTime(start="2026-07-12T00:00:00+00:00", basis="user_explicit"),
        created_from_event_id=event.event_id,
    )
    evidence = Evidence(
        evidence_id="evd_windows_path_001",
        claim_id=claim.claim_id,
        event_id=event.event_id,
        relation="supports",
        fragment={"path": "输入 记录.jsonl", "line_start": 1, "line_end": 1},
        quoted_text_hash="sha256:" + "c" * 64,
    )
    decision = Decision(
        decision_id="dec_windows_path_001",
        action="admit",
        result_claim_id=claim.claim_id,
        result_status="accepted",
        policy={
            "engine_version": "windows-acceptance-v1",
            "governance": {"can_recall": True, "can_inject": True, "can_act_on": False},
        },
        actor=Actor(kind="user", id="sam"),
        reason="Windows platform acceptance fixture",
        recorded_at=RECORDED_AT,
    )
    return LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_windows_path_001",
        recorded_at=RECORDED_AT,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=claim.to_dict()),
            LedgerEvent(type="evidence.linked", payload=evidence.to_dict()),
            LedgerEvent(type="decision.made", payload=decision.to_dict()),
        ),
    )


def _runtime(root: Path) -> tuple[JsonlRecordStore, ProjectionCoordinator, dict[str, Path]]:
    ledger_root = root / "记忆 数据" / "ledger-v1"
    projection_root = root / "记忆 数据" / "projection files"
    paths = {
        "sqlite": projection_root / "memory 数据.sqlite3",
        "search": projection_root / "search 索引.json",
        "fts": projection_root / "fts 索引.json",
        "vector": projection_root / "vector 索引.json",
        "graph": projection_root / "graph 图谱.json",
    }
    store = JsonlRecordStore(ledger_root)
    store.append(_transaction(), expected_head=GENESIS_HASH)
    coordinator = ProjectionCoordinator(
        store,
        (
            SQLiteProjectionAdapter(paths["sqlite"]),
            SearchProjectionAdapter(paths["search"]),
            FtsProjectionAdapter(paths["fts"]),
            VectorProjectionAdapter(paths["vector"]),
            GraphProjectionAdapter(paths["graph"]),
        ),
    )
    return store, coordinator, paths


def test_unicode_space_workspace_rebuilds_every_projection(tmp_path: Path) -> None:
    workspace = tmp_path / "Windows 验收 workspace with spaces"
    store, coordinator, paths = _runtime(workspace)
    first = coordinator.rebuild_all()

    assert tuple(item.descriptor.name for item in first.projections) == (
        "sqlite",
        "search",
        "fts",
        "vector",
        "graph",
    )
    assert all(path.is_file() for path in paths.values())
    expected_head = str(store.verify().last_valid_hash)

    for path in paths.values():
        path.unlink()
    assert coordinator.status().ready_for_query is False

    recovery = ProjectionRecoveryService(coordinator)
    rebuilt = recovery.rebuild(
        expected_head,
        apply=True,
        confirmation=recovery.confirmation_token(expected_head),
    )

    assert rebuilt.applied is True
    assert rebuilt.ready_after is True
    assert rebuilt.rebuilt_projections == ("sqlite", "search", "fts", "vector", "graph")
    assert all(path.is_file() for path in paths.values())
    status = coordinator.require_ready_for_query()
    assert status.ledger_head == expected_head
    assert all(item.logical_state_hash == status.logical_state_hash for item in status.freshness)


def test_windows_style_paths_preserve_drive_and_components() -> None:
    candidate = PureWindowsPath(r"C:\Users\Sam User\MS8 验收\memory\ledger\events.jsonl")

    assert candidate.drive == "C:"
    assert candidate.name == "events.jsonl"
    assert candidate.parts[-3:] == ("memory", "ledger", "events.jsonl")
    assert "Sam User" in candidate.parts
    assert "MS8 验收" in candidate.parts


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path resolution")
def test_windows_runtime_paths_remain_inside_workspace(tmp_path: Path) -> None:
    workspace = (tmp_path / "Windows 用户" / "MS8 workspace").resolve()
    store, coordinator, paths = _runtime(workspace)
    coordinator.rebuild_all()

    assert store.ledger_path.resolve().is_relative_to(workspace)
    assert store.manifest_path.resolve().is_relative_to(workspace)
    assert all(path.resolve().is_relative_to(workspace) for path in paths.values())
