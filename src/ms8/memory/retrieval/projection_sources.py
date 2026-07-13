"""Concrete candidate providers for current Ledger v1 projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..infrastructure.projection_io import read_json_object
from ..infrastructure.search_projection import SEARCH_PROJECTION_SCHEMA
from .adapters import CandidateRecord
from .analyzer import analyze_query
from .models import RetrievalPlan


class SearchProjectionFormatError(RuntimeError):
    """Raised when a disposable search projection cannot be trusted."""


@runtime_checkable
class EvidenceResolver(Protocol):
    """Resolve evidence identifiers from the authoritative replay state."""

    def __call__(self, claim_id: str) -> Sequence[str]: ...


def _required_mapping(value: object, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SearchProjectionFormatError(message)
    return value


def _required_sequence(value: object, message: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise SearchProjectionFormatError(message)
    return value


class SearchProjectionCandidateProvider:
    """Read the current JSON Search/FTS projection inside an eligibility whitelist.

    The projection remains disposable and non-authoritative.  Evidence identifiers
    are resolved separately from the verified replay state supplied by the caller.
    """

    def __init__(self, artifact_path: Path, evidence_resolver: EvidenceResolver) -> None:
        self.artifact_path = Path(artifact_path)
        self.evidence_resolver = evidence_resolver

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

        payload = _required_mapping(
            read_json_object(self.artifact_path),
            "search projection is missing or unreadable",
        )
        manifest = _required_mapping(payload.get("manifest"), "search projection manifest is invalid")
        if str(manifest.get("schema") or "") != SEARCH_PROJECTION_SCHEMA:
            raise SearchProjectionFormatError("search projection schema mismatch")
        documents = _required_sequence(payload.get("documents"), "search projection documents are invalid")
        postings = _required_mapping(payload.get("postings"), "search projection postings are invalid")

        eligible_set = frozenset(eligible_claim_ids)
        document_by_id: dict[str, Mapping[str, Any]] = {}
        for raw_document in documents:
            document = _required_mapping(raw_document, "search projection contains an invalid document")
            claim_id = str(document.get("claim_id") or "").strip()
            if not claim_id:
                raise SearchProjectionFormatError("search projection contains an empty claim identifier")
            if claim_id in document_by_id:
                raise SearchProjectionFormatError("search projection contains duplicate claim identifiers")
            if claim_id in eligible_set:
                document_by_id[claim_id] = document

        analysis = analyze_query(plan.query.text)
        query_terms = frozenset(str(term).casefold() for term in analysis.tokens if str(term).strip())
        if not query_terms:
            return ()

        candidate_ids: set[str] = set()
        for term in sorted(query_terms):
            raw_claim_ids = postings.get(term, ())
            for raw_claim_id in _required_sequence(
                raw_claim_ids,
                f"search projection posting is invalid: {term}",
            ):
                claim_id = str(raw_claim_id)
                if claim_id in document_by_id:
                    candidate_ids.add(claim_id)

        records: list[CandidateRecord] = []
        normalized_query = analysis.normalized_text
        for claim_id in sorted(candidate_ids):
            document = document_by_id[claim_id]
            raw_terms = _required_sequence(
                document.get("terms", ()),
                f"search projection terms are invalid: {claim_id}",
            )
            document_terms = frozenset(str(term).casefold() for term in raw_terms if str(term).strip())
            matched_terms = tuple(sorted(query_terms.intersection(document_terms)))
            if not matched_terms:
                continue
            evidence_ids = tuple(
                sorted(
                    {
                        str(value).strip()
                        for value in self.evidence_resolver(claim_id)
                        if str(value).strip()
                    }
                )
            )
            if not evidence_ids:
                continue
            text = str(document.get("text") or "")
            score = len(matched_terms) / max(1, len(query_terms))
            if normalized_query and normalized_query in text.casefold():
                score += 1.0
            records.append(
                CandidateRecord(
                    claim_id=claim_id,
                    evidence_ids=evidence_ids,
                    score=score,
                    reason={
                        "projection_schema": SEARCH_PROJECTION_SCHEMA,
                        "matched_terms": matched_terms,
                    },
                )
            )

        records.sort(key=lambda item: (-item.score, item.claim_id, item.evidence_ids))
        return tuple(records[:limit])


__all__ = [
    "EvidenceResolver",
    "SearchProjectionCandidateProvider",
    "SearchProjectionFormatError",
]
