"""Deterministic entity and alias candidate retrieval for Hybrid Retrieval v1."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from ..infrastructure.projection_io import read_json_object
from ..infrastructure.search_projection import SEARCH_PROJECTION_SCHEMA
from .adapters import CandidateRecord, ProjectionCandidateSource
from .analyzer import analyze_query
from .models import RetrievalPlan


class EntityProjectionFormatError(RuntimeError):
    """Raised when structured entity fields cannot be trusted."""


def normalize_entity_label(value: object) -> str:
    """Normalize an entity label without semantic expansion or model inference."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return " ".join(text.split())


def _required_mapping(value: object, message: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EntityProjectionFormatError(message)
    return value


def _required_sequence(value: object, message: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise EntityProjectionFormatError(message)
    return value


def _entity_mentions(plan: RetrievalPlan) -> tuple[str, ...]:
    analysis = analyze_query(plan.query.text)
    raw_mentions: list[object] = [
        *plan.entity_mentions,
        *analysis.exact_tokens,
        *analysis.code_tokens,
    ]
    if len(analysis.tokens) <= 8:
        raw_mentions.extend(analysis.tokens)
    normalized_query = normalize_entity_label(analysis.normalized_text)
    if normalized_query:
        raw_mentions.append(normalized_query)
    mentions = {normalize_entity_label(value) for value in raw_mentions}
    return tuple(sorted(value for value in mentions if value))


def _document_labels(document: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    labels: list[tuple[str, str]] = []
    subject = normalize_entity_label(document.get("subject"))
    if subject:
        labels.append(("subject", subject))
    raw_aliases = document.get("aliases", ())
    for alias in _required_sequence(raw_aliases, "search projection aliases are invalid"):
        normalized = normalize_entity_label(alias)
        if normalized:
            labels.append(("alias", normalized))
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in labels:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return tuple(unique)


def _best_match(
    labels: Sequence[tuple[str, str]],
    mentions: Sequence[str],
    normalized_query: str,
) -> tuple[float, str, str] | None:
    candidates: list[tuple[float, str, str]] = []
    mention_set = frozenset(mentions)
    for field_name, label in labels:
        if label in mention_set:
            score = 1.0 if field_name == "subject" else 0.95
            candidates.append((score, field_name, label))
            continue
        if len(label) >= 2 and label in normalized_query:
            score = 0.85 if field_name == "subject" else 0.80
            candidates.append((score, field_name, label))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    return candidates[0]


class EntityProjectionCandidateProvider:
    """Match structured subjects and aliases inside an eligibility whitelist."""

    def __init__(
        self,
        artifact_path: Path,
        evidence_resolver: Callable[[str], Sequence[str]],
    ) -> None:
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
        if manifest.get("schema") != SEARCH_PROJECTION_SCHEMA:
            raise EntityProjectionFormatError("search projection schema mismatch")
        documents = _required_sequence(payload.get("documents"), "search projection documents are invalid")

        eligible = frozenset(eligible_claim_ids)
        mentions = _entity_mentions(plan)
        normalized_query = normalize_entity_label(plan.query.text)
        records: list[CandidateRecord] = []
        for raw_document in documents:
            document = _required_mapping(raw_document, "search projection contains an invalid document")
            claim_id = str(document.get("claim_id") or "").strip()
            if not claim_id:
                raise EntityProjectionFormatError("search projection contains an empty claim identifier")
            if claim_id not in eligible:
                continue
            match = _best_match(_document_labels(document), mentions, normalized_query)
            if match is None:
                continue
            score, field_name, matched_label = match
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
            records.append(
                CandidateRecord(
                    claim_id=claim_id,
                    evidence_ids=evidence_ids,
                    score=score,
                    reason={
                        "projection_schema": SEARCH_PROJECTION_SCHEMA,
                        "entity_field": field_name,
                        "matched_entity": matched_label,
                        "match_kind": "exact" if matched_label in mentions else "contained",
                    },
                )
            )
        records.sort(key=lambda item: (-item.score, item.claim_id, item.evidence_ids))
        return tuple(records[:limit])


class EntityProjectionCandidateSource(ProjectionCandidateSource):
    """Entity-channel adapter for deterministic structured alias matching."""

    def __init__(self, provider: EntityProjectionCandidateProvider) -> None:
        super().__init__(name="entity-projection", channel="entity", provider=provider)


__all__ = [
    "EntityProjectionCandidateProvider",
    "EntityProjectionCandidateSource",
    "EntityProjectionFormatError",
    "normalize_entity_label",
]
