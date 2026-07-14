from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ms8.memory.application.replay import ClaimReplayView, ReplayState
from ms8.memory.domain.models import Claim, ValidTime
from ms8.memory.infrastructure.embedding_projection import (
    EmbeddingProjectionEntry,
    EmbeddingProjectionSnapshot,
    embedding_source_content_hash,
    read_embedding_projection,
    write_embedding_projection,
)
from ms8.memory.retrieval.adapters import run_candidate_sources
from ms8.memory.retrieval.eligibility import EligibleClaims
from ms8.memory.retrieval.embedding import EmbeddingMatch
from ms8.memory.retrieval.embedding_sources import (
    EmbeddingProjectionCandidateProvider,
    EmbeddingProjectionCandidateSource,
)
from ms8.memory.retrieval.models import MemoryQuery, Principal, RetrievalPlan
from ms8.memory.retrieval.ollama_embedding import OllamaEmbeddingProvider


class _Provider:
    model_id = "local:test-v1"
    dimensions = 2

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0) for _text in texts)


class _FailingProvider(_Provider):
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        del texts
        raise RuntimeError("embedding provider unavailable")


class _UnsafeBackend:
    name = "unsafe-test"

    def search(
        self,
        query_vector: tuple[float, ...],
        eligible_claim_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[EmbeddingMatch, ...]:
        del query_vector, eligible_claim_ids, limit
        return (EmbeddingMatch(claim_id="claim:blocked", score=1.0),)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _plan() -> RetrievalPlan:
    principal = Principal(
        principal_id="user:test",
        kind="user",
        realm_ids=("realm:test",),
        scopes=("project:test",),
        capabilities=("all",),
    )
    return RetrievalPlan(
        query=MemoryQuery(
            text="hybrid retrieval",
            realm_ids=("realm:test",),
            scope="project:test",
        ),
        principal=principal,
        intent="open_recall",
        realm_ids=("realm:test",),
    )


def _state() -> ReplayState:
    claims = {
        claim_id: ClaimReplayView(
            claim=Claim(
                claim_id=claim_id,
                kind="fact",
                text=text,
                subject="MS8",
                predicate="retrieval",
                value=text,
                scope="project:test",
                realm_id="realm:test",
                authority="user_explicit",
                sensitivity="internal",
                confidence=1.0,
                status="verified",
                valid_time=ValidTime(start="2026-07-01T00:00:00Z", basis="user_explicit"),
                created_from_event_id=f"event:{claim_id}",
            ),
            current_status="verified",
            decision_ids=(),
        )
        for claim_id, text in (
            ("claim:allowed", "hybrid retrieval"),
            ("claim:blocked", "blocked retrieval"),
        )
    }
    return ReplayState(
        ledger_head="sha256:ledger",
        last_sequence=2,
        memory_events={},
        claims=claims,
        evidence={},
        decisions={},
        conflicts={},
        logical_state_hash="sha256:state",
    )
def _write_snapshot(path: Path) -> None:
    write_embedding_projection(
        path,
        EmbeddingProjectionSnapshot(
            model_id="local:test-v1",
            dimensions=2,
            built_from_ledger_head="sha256:ledger",
            last_sequence=2,
            logical_state_hash="sha256:state",
            entries=(
                EmbeddingProjectionEntry(
                    claim_id="claim:allowed",
                    content_hash="sha256:allowed",
                    vector=(1.0, 0.0),
                ),
                EmbeddingProjectionEntry(
                    claim_id="claim:blocked",
                    content_hash="sha256:blocked",
                    vector=(1.0, 0.0),
                ),
            ),
        ),
    )


def test_embedding_projection_source_scores_only_eligible_claims(tmp_path: Path) -> None:
    path = tmp_path / "embedding.json"
    _write_snapshot(path)
    resolved: list[str] = []

    def resolve_evidence(claim_id: str) -> tuple[str, ...]:
        resolved.append(claim_id)
        return (f"evidence:{claim_id}",)

    provider = EmbeddingProjectionCandidateProvider(
        path,
        _Provider(),
        resolve_evidence,
        state=_state(),
    )
    source = EmbeddingProjectionCandidateSource(provider)
    eligible = EligibleClaims(claim_ids=("claim:allowed",), evaluated_count=2)

    batch = run_candidate_sources((source,), _plan(), eligible)

    hits = batch.hits_by_source["embedding-projection"]
    assert [item.claim_id for item in hits] == ["claim:allowed"]
    assert resolved == ["claim:allowed"]
    assert hits[0].reason["backend"] == "exact-cosine"
    assert hits[0].reason["model_id"] == "local:test-v1"
    snapshot = read_embedding_projection(path)
    assert snapshot is not None
    assert snapshot.source_content_hashes == {
        "claim:allowed": embedding_source_content_hash("hybrid retrieval")
    }


def test_failed_embedding_projection_rebuild_degrades_without_widening(tmp_path: Path) -> None:
    provider = EmbeddingProjectionCandidateProvider(
        tmp_path / "missing.json",
        _FailingProvider(),
        lambda claim_id: (f"evidence:{claim_id}",),
        state=_state(),
    )
    source = EmbeddingProjectionCandidateSource(provider)
    eligible = EligibleClaims(claim_ids=("claim:allowed",), evaluated_count=1)

    batch = run_candidate_sources((source,), _plan(), eligible)

    assert batch.hits_by_source["embedding-projection"] == ()
    assert batch.degradation_reasons == (
        "embedding-projection:RuntimeError",
    )


def test_approximate_backend_cannot_return_unauthorized_claim(tmp_path: Path) -> None:
    path = tmp_path / "embedding.json"
    _write_snapshot(path)
    provider = EmbeddingProjectionCandidateProvider(
        path,
        _Provider(),
        lambda claim_id: (f"evidence:{claim_id}",),
        state=_state(),
        approximate_backend=_UnsafeBackend(),
    )
    source = EmbeddingProjectionCandidateSource(provider)
    eligible = EligibleClaims(claim_ids=("claim:allowed",), evaluated_count=2)

    with pytest.raises(PermissionError, match="outside the retrieval eligibility set"):
        run_candidate_sources((source,), _plan(), eligible)


def test_ollama_provider_rejects_remote_host_without_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="allow_remote=True"):
        OllamaEmbeddingProvider(
            model="nomic-embed-text",
            dimensions=2,
            host="https://example.test:11434",
        )


def test_ollama_provider_uses_http_without_python_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        requests.append(request)
        assert timeout == 2.0
        return _Response({"embeddings": [[1.0, 0.0], [0.0, 1.0]]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = OllamaEmbeddingProvider(
        model="nomic-embed-text",
        dimensions=2,
        timeout_seconds=2.0,
    )

    vectors = provider.embed(("one", "two"))

    assert provider.model_id == "ollama:nomic-embed-text"
    assert vectors == ((1.0, 0.0), (0.0, 1.0))
    assert len(requests) == 1
    assert requests[0].full_url == "http://127.0.0.1:11434/api/embed"
