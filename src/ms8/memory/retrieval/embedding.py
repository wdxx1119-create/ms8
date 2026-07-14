"""Provider-neutral embedding contracts for governed Hybrid Retrieval v1.

Embedding generation is deliberately separated from projection storage and ranking.
Exact search receives an immutable eligibility whitelist and only reads vectors for
claim identifiers already authorized by the caller.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .eligibility import EligibleClaims


class EmbeddingSearchError(RuntimeError):
    """Raised when embedding data violates the deterministic search contract."""


@dataclass(frozen=True, slots=True)
class EmbeddingMatch:
    claim_id: str
    score: float

    def __post_init__(self) -> None:
        claim_id = str(self.claim_id or "").strip()
        if not claim_id:
            raise ValueError("embedding match claim_id must not be empty")
        score = float(self.score)
        if not math.isfinite(score):
            raise ValueError("embedding match score must be finite")
        object.__setattr__(self, "claim_id", claim_id)
        object.__setattr__(self, "score", score)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Generate vectors without coupling Hybrid Retrieval to one model runtime."""

    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


@runtime_checkable
class ApproximateEmbeddingBackend(Protocol):
    """Optional ANN boundary reserved for HNSW or another local backend."""

    @property
    def name(self) -> str: ...

    def search(
        self,
        query_vector: Sequence[float],
        eligible_claim_ids: tuple[str, ...],
        limit: int,
    ) -> Sequence[EmbeddingMatch]: ...


def normalize_embedding_vector(
    values: Sequence[float],
    *,
    field_name: str,
    expected_dimensions: int | None = None,
    allow_zero: bool = True,
) -> tuple[float, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{field_name} must be a numeric sequence")
    vector = tuple(float(value) for value in values)
    if not vector:
        raise ValueError(f"{field_name} must not be empty")
    if expected_dimensions is not None and len(vector) != expected_dimensions:
        raise ValueError(
            f"{field_name} dimensions mismatch: expected={expected_dimensions} actual={len(vector)}"
        )
    if any(not math.isfinite(value) for value in vector):
        raise ValueError(f"{field_name} must contain only finite values")
    if not allow_zero and not any(value != 0.0 for value in vector):
        raise ValueError(f"{field_name} must not be a zero vector")
    return vector


def validate_embedding_batch(
    provider: EmbeddingProvider,
    texts: Sequence[str],
    vectors: Sequence[Sequence[float]],
) -> tuple[tuple[float, ...], ...]:
    """Validate one provider response before it can enter a projection artifact."""

    model_id = str(provider.model_id or "").strip()
    if not model_id:
        raise ValueError("embedding provider model_id must not be empty")
    dimensions = provider.dimensions
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
        raise ValueError("embedding provider dimensions must be a positive integer")
    if len(texts) != len(vectors):
        raise ValueError("embedding provider output count must match input count")
    return tuple(
        normalize_embedding_vector(
            vector,
            field_name=f"embedding provider vector[{index}]",
            expected_dimensions=dimensions,
        )
        for index, vector in enumerate(vectors)
    )


def exact_cosine_search(
    *,
    query_vector: Sequence[float],
    vectors: Mapping[str, Sequence[float]],
    eligible: EligibleClaims,
    limit: int,
) -> tuple[EmbeddingMatch, ...]:
    """Search only authorized claim vectors using deterministic exact cosine.

    Iteration is driven by ``eligible.claim_ids`` rather than the projection mapping,
    so vectors outside the authorization set are never inspected or scored.
    """

    if not isinstance(eligible, EligibleClaims):
        raise TypeError("eligible must be EligibleClaims")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("embedding search limit must be a positive integer")

    query = normalize_embedding_vector(
        query_vector,
        field_name="query_vector",
        allow_zero=False,
    )
    query_norm = math.sqrt(sum(value * value for value in query))
    matches: list[EmbeddingMatch] = []

    for claim_id in eligible.claim_ids:
        raw_vector = vectors.get(claim_id)
        if raw_vector is None:
            continue
        try:
            vector = normalize_embedding_vector(
                raw_vector,
                field_name=f"embedding vector {claim_id}",
                expected_dimensions=len(query),
            )
        except (TypeError, ValueError) as exc:
            raise EmbeddingSearchError(str(exc)) from exc
        vector_norm = math.sqrt(sum(value * value for value in vector))
        if vector_norm == 0.0:
            continue
        score = sum(left * right for left, right in zip(query, vector)) / (query_norm * vector_norm)
        if score <= 0.0:
            continue
        matches.append(EmbeddingMatch(claim_id=claim_id, score=round(score, 12)))

    matches.sort(key=lambda item: (-item.score, item.claim_id))
    return tuple(matches[:limit])


__all__ = [
    "ApproximateEmbeddingBackend",
    "EmbeddingMatch",
    "EmbeddingProvider",
    "EmbeddingSearchError",
    "exact_cosine_search",
    "normalize_embedding_vector",
    "validate_embedding_batch",
]
