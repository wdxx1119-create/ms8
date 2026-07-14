from __future__ import annotations

import json
from pathlib import Path

import pytest

from ms8.memory.infrastructure.embedding_projection import (
    EMBEDDING_PROJECTION_SCHEMA,
    EmbeddingProjectionEntry,
    EmbeddingProjectionSnapshot,
    embedding_projection_rebuild_reasons,
    read_embedding_projection,
    write_embedding_projection,
)
from ms8.memory.infrastructure.vector_projection import (
    VECTOR_BUILDER_VERSION,
    VECTOR_PROJECTION_SCHEMA,
)
from ms8.memory.retrieval.embedding import (
    EmbeddingProvider,
    exact_cosine_search,
    validate_embedding_batch,
)
from ms8.memory.retrieval.eligibility import EligibleClaims


class _Provider:
    model_id = "local:test-v1"
    dimensions = 2

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(index + 1), 1.0) for index, _text in enumerate(texts))


def _snapshot() -> EmbeddingProjectionSnapshot:
    return EmbeddingProjectionSnapshot(
        model_id="local:test-v1",
        dimensions=2,
        built_from_ledger_head="sha256:ledger",
        last_sequence=3,
        logical_state_hash="sha256:state",
        entries=(
            EmbeddingProjectionEntry(
                claim_id="claim:b",
                content_hash="sha256:content-b",
                vector=(0.0, 1.0),
            ),
            EmbeddingProjectionEntry(
                claim_id="claim:a",
                content_hash="sha256:content-a",
                vector=(1.0, 0.0),
            ),
        ),
    )


def test_embedding_provider_contract_and_batch_validation() -> None:
    provider = _Provider()

    assert isinstance(provider, EmbeddingProvider)
    vectors = validate_embedding_batch(provider, ("one", "two"), provider.embed(("one", "two")))

    assert vectors == ((1.0, 1.0), (2.0, 1.0))


def test_embedding_projection_round_trip_binds_model_and_content(tmp_path: Path) -> None:
    path = tmp_path / "embedding.json"
    snapshot = _snapshot()

    artifact_hash = write_embedding_projection(path, snapshot)
    restored = read_embedding_projection(path)

    assert artifact_hash.startswith("sha256:")
    assert restored == snapshot
    assert restored is not None
    assert restored.to_payload()["manifest"]["schema"] == EMBEDDING_PROJECTION_SCHEMA
    assert list(restored.vectors) == ["claim:a", "claim:b"]


def test_embedding_projection_detects_model_dimension_and_content_changes() -> None:
    snapshot = _snapshot()

    assert embedding_projection_rebuild_reasons(
        snapshot,
        model_id="local:test-v1",
        dimensions=2,
        content_hashes={
            "claim:a": "sha256:content-a",
            "claim:b": "sha256:content-b",
        },
    ) == ()
    assert embedding_projection_rebuild_reasons(
        snapshot,
        model_id="local:test-v2",
        dimensions=3,
        content_hashes={"claim:a": "sha256:changed"},
    ) == ("model_id_mismatch", "dimensions_mismatch", "content_set_mismatch")
    assert embedding_projection_rebuild_reasons(
        snapshot,
        model_id="local:test-v1",
        dimensions=2,
        content_hashes={
            "claim:a": "sha256:changed",
            "claim:b": "sha256:content-b",
        },
    ) == ("content_hash_mismatch",)


def test_exact_cosine_search_only_inspects_eligible_claim_vectors() -> None:
    eligible = EligibleClaims(claim_ids=("claim:allowed",), evaluated_count=2)
    vectors = {
        "claim:allowed": (1.0, 0.0),
        # This malformed unauthorized vector must never be inspected or scored.
        "claim:blocked": ("not-a-number",),
    }

    matches = exact_cosine_search(
        query_vector=(1.0, 0.0),
        vectors=vectors,  # type: ignore[arg-type]
        eligible=eligible,
        limit=5,
    )

    assert [(item.claim_id, item.score) for item in matches] == [("claim:allowed", 1.0)]


def test_exact_cosine_search_rejects_zero_query_vector() -> None:
    eligible = EligibleClaims(claim_ids=("claim:a",), evaluated_count=1)

    with pytest.raises(ValueError, match="zero vector"):
        exact_cosine_search(
            query_vector=(0.0, 0.0),
            vectors={"claim:a": (1.0, 0.0)},
            eligible=eligible,
            limit=1,
        )


def test_embedding_projection_rejects_tampered_content(tmp_path: Path) -> None:
    path = tmp_path / "embedding.json"
    write_embedding_projection(path, _snapshot())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["embeddings"][0]["content_hash"] = "sha256:tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert read_embedding_projection(path) is None


def test_existing_deterministic_vector_projection_contract_is_unchanged() -> None:
    assert VECTOR_PROJECTION_SCHEMA == "ms8.vector_projection.v1"
    assert VECTOR_BUILDER_VERSION == "1"
