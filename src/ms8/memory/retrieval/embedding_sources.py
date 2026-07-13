"""Eligibility-restricted candidate provider for embedding projections."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from ..infrastructure.embedding_projection import (
    EMBEDDING_PROJECTION_SCHEMA,
    read_embedding_projection,
)
from .adapters import CandidateRecord, ProjectionCandidateSource
from .embedding import (
    ApproximateEmbeddingBackend,
    EmbeddingMatch,
    EmbeddingProvider,
    exact_cosine_search,
    validate_embedding_batch,
)
from .eligibility import EligibleClaims
from .models import RetrievalPlan


class EmbeddingProjectionFormatError(RuntimeError):
    """Raised when an embedding artifact cannot safely provide candidates."""


class EmbeddingProjectionCandidateProvider:
    """Generate vector candidates from one versioned embedding artifact.

    The provider receives only authorized claim identifiers. Exact search iterates
    that whitelist; optional ANN backends receive the same immutable tuple and their
    output is validated before it becomes a candidate record.
    """

    def __init__(
        self,
        artifact_path: Path,
        embedding_provider: EmbeddingProvider,
        evidence_resolver: Callable[[str], Sequence[str]],
        *,
        approximate_backend: ApproximateEmbeddingBackend | None = None,
    ) -> None:
        self.artifact_path = Path(artifact_path)
        self.embedding_provider = embedding_provider
        self.evidence_resolver = evidence_resolver
        self.approximate_backend = approximate_backend

    def __call__(
        self,
        plan: RetrievalPlan,
        eligible_claim_ids: tuple[str, ...],
        limit: int,
    ) -> Sequence[CandidateRecord]:
        if not isinstance(plan, RetrievalPlan):
            raise TypeError("plan must be RetrievalPlan")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")

        snapshot = read_embedding_projection(self.artifact_path)
        if snapshot is None:
            raise EmbeddingProjectionFormatError("embedding projection is missing or invalid")
        if snapshot.model_id != self.embedding_provider.model_id:
            raise EmbeddingProjectionFormatError("embedding projection model_id mismatch")
        if snapshot.dimensions != self.embedding_provider.dimensions:
            raise EmbeddingProjectionFormatError("embedding projection dimensions mismatch")

        vectors = self.embedding_provider.embed((plan.query.text,))
        query_batch = validate_embedding_batch(
            self.embedding_provider,
            (plan.query.text,),
            vectors,
        )
        query_vector = query_batch[0]
        eligible = EligibleClaims(
            claim_ids=eligible_claim_ids,
            evaluated_count=len(eligible_claim_ids),
        )
        if self.approximate_backend is None:
            matches = exact_cosine_search(
                query_vector=query_vector,
                vectors=snapshot.vectors,
                eligible=eligible,
                limit=limit,
            )
            backend_name = "exact-cosine"
        else:
            backend_name = str(self.approximate_backend.name or "").strip()
            if not backend_name:
                raise EmbeddingProjectionFormatError("approximate backend name must not be empty")
            raw_matches = self.approximate_backend.search(
                query_vector,
                eligible.claim_ids,
                limit,
            )
            matches = self._validate_backend_matches(raw_matches, eligible=eligible, limit=limit)

        records: list[CandidateRecord] = []
        for match in matches:
            evidence_ids = tuple(
                sorted(
                    {
                        str(value).strip()
                        for value in self.evidence_resolver(match.claim_id)
                        if str(value).strip()
                    }
                )
            )
            if not evidence_ids:
                continue
            records.append(
                CandidateRecord(
                    claim_id=match.claim_id,
                    evidence_ids=evidence_ids,
                    score=match.score,
                    reason={
                        "projection_schema": EMBEDDING_PROJECTION_SCHEMA,
                        "model_id": snapshot.model_id,
                        "backend": backend_name,
                    },
                )
            )
        return tuple(records)

    @staticmethod
    def _validate_backend_matches(
        matches: Sequence[EmbeddingMatch],
        *,
        eligible: EligibleClaims,
        limit: int,
    ) -> tuple[EmbeddingMatch, ...]:
        if len(matches) > limit:
            raise EmbeddingProjectionFormatError("approximate backend exceeded candidate limit")
        seen: set[str] = set()
        validated: list[EmbeddingMatch] = []
        for match in matches:
            if not isinstance(match, EmbeddingMatch):
                raise EmbeddingProjectionFormatError(
                    "approximate backend returned a non-EmbeddingMatch value"
                )
            eligible.require(match.claim_id)
            if match.claim_id in seen:
                raise EmbeddingProjectionFormatError(
                    "approximate backend returned duplicate claim identifiers"
                )
            seen.add(match.claim_id)
            validated.append(match)
        validated.sort(key=lambda item: (-item.score, item.claim_id))
        return tuple(validated)


class EmbeddingProjectionCandidateSource(ProjectionCandidateSource):
    """Vector-channel adapter for the governed embedding projection provider."""

    def __init__(self, provider: EmbeddingProjectionCandidateProvider) -> None:
        super().__init__(name="embedding-projection", channel="vector", provider=provider)


__all__ = [
    "EmbeddingProjectionCandidateProvider",
    "EmbeddingProjectionCandidateSource",
    "EmbeddingProjectionFormatError",
]
