"""Portable deterministic full-text projection derived from ledger replay state."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..application.replay import ClaimReplayView, ReplayState
from ..application.temporal_query import effective_valid_until
from ..domain.ledger import canonical_json
from ..ports.projection import ProjectionBuildResult, ProjectionDescriptor, ProjectionFreshness
from .projection_io import atomic_write_json, read_json_object, sha256_bytes
from .search_projection import _terms

FTS_PROJECTION_NAME = "fts"
FTS_PROJECTION_SCHEMA = "ms8.fts_projection.v1"
FTS_BUILDER_VERSION = "1"


def _content_hash(documents: object, postings: object) -> str:
    payload = canonical_json({"documents": documents, "postings": postings}).encode("utf-8")
    return sha256_bytes(payload)


def _latest_action(state: ReplayState, view: ClaimReplayView) -> str | None:
    if not view.decision_ids:
        return None
    decision = state.decisions.get(view.decision_ids[-1])
    return decision.action if decision is not None else None


def _document(state: ReplayState, claim_id: str) -> dict[str, Any] | None:
    view = state.claims[claim_id]
    if _latest_action(state, view) == "forget":
        return None
    claim = view.claim
    searchable = " ".join(
        (
            claim.text,
            claim.subject,
            claim.predicate,
            claim.scope,
            claim.realm_id,
            claim.authority,
            claim.sensitivity,
        )
    )
    return {
        "claim_id": claim.claim_id,
        "text": claim.text,
        "subject": claim.subject,
        "predicate": claim.predicate,
        "scope": claim.scope,
        "realm_id": claim.realm_id,
        "authority": claim.authority,
        "sensitivity": claim.sensitivity,
        "confidence": claim.confidence,
        "current_status": view.current_status,
        "valid_from": claim.valid_time.start,
        "valid_until": effective_valid_until(state, view),
        "terms": list(_terms(searchable)),
    }


class FtsProjectionAdapter:
    """Build a full-text inverted index as a disposable portable projection."""

    def __init__(self, artifact_path: Path):
        self.artifact_path = Path(artifact_path)

    @property
    def name(self) -> str:
        return FTS_PROJECTION_NAME

    def rebuild_from_state(self, source: ReplayState) -> ProjectionBuildResult:
        documents: list[dict[str, Any]] = []
        for claim_id in sorted(source.claims):
            item = _document(source, claim_id)
            if item is not None:
                documents.append(item)
        postings: dict[str, list[str]] = {}
        for document in documents:
            claim_id = str(document["claim_id"])
            terms = document.get("terms")
            if not isinstance(terms, list):
                raise TypeError("fts document terms must be a list")
            for term in terms:
                postings.setdefault(str(term), []).append(claim_id)
        ordered_postings = {
            term: sorted(claim_ids)
            for term, claim_ids in sorted(postings.items())
        }
        payload = {
            "manifest": {
                "name": self.name,
                "schema": FTS_PROJECTION_SCHEMA,
                "builder_version": FTS_BUILDER_VERSION,
                "built_from_ledger_head": source.ledger_head,
                "last_sequence": source.last_sequence,
                "logical_state_hash": source.logical_state_hash,
                "document_count": len(documents),
                "term_count": len(ordered_postings),
                "content_hash": _content_hash(documents, ordered_postings),
            },
            "documents": documents,
            "postings": ordered_postings,
        }
        replaced = self.artifact_path.exists()
        artifact_hash = atomic_write_json(self.artifact_path, payload)
        return ProjectionBuildResult(
            descriptor=ProjectionDescriptor(
                name=self.name,
                schema=FTS_PROJECTION_SCHEMA,
                artifact_path=self.artifact_path,
                built_from_ledger_head=source.ledger_head,
                last_sequence=source.last_sequence,
                logical_state_hash=source.logical_state_hash,
                builder_version=FTS_BUILDER_VERSION,
                artifact_hash=artifact_hash,
            ),
            replaced_existing=replaced,
        )

    def read_descriptor(self) -> ProjectionDescriptor | None:
        payload = read_json_object(self.artifact_path)
        manifest = payload.get("manifest") if isinstance(payload, dict) else None
        documents = payload.get("documents") if isinstance(payload, dict) else None
        postings = payload.get("postings") if isinstance(payload, dict) else None
        if not isinstance(manifest, Mapping) or not isinstance(documents, list) or not isinstance(postings, dict):
            return None
        if manifest.get("content_hash") != _content_hash(documents, postings):
            return None
        if manifest.get("document_count") != len(documents) or manifest.get("term_count") != len(postings):
            return None
        try:
            return ProjectionDescriptor(
                name=str(manifest["name"]),
                schema=str(manifest["schema"]),
                artifact_path=self.artifact_path,
                built_from_ledger_head=str(manifest["built_from_ledger_head"]),
                last_sequence=int(manifest["last_sequence"]),
                logical_state_hash=str(manifest["logical_state_hash"]),
                builder_version=str(manifest["builder_version"]),
                artifact_hash=sha256_bytes(self.artifact_path.read_bytes()),
            )
        except (KeyError, OSError, TypeError, ValueError):
            return None

    def freshness(self, ledger_head: str) -> ProjectionFreshness:
        descriptor = self.read_descriptor()
        if descriptor is None:
            return ProjectionFreshness(
                name=self.name,
                exists=self.artifact_path.exists(),
                fresh=False,
                projection_head=None,
                ledger_head=ledger_head,
                reason="projection_missing_or_invalid",
            )
        if descriptor.schema != FTS_PROJECTION_SCHEMA or descriptor.name != self.name:
            return ProjectionFreshness(
                name=self.name,
                exists=True,
                fresh=False,
                projection_head=descriptor.built_from_ledger_head,
                ledger_head=ledger_head,
                reason="projection_schema_mismatch",
                logical_state_hash=descriptor.logical_state_hash,
            )
        if descriptor.built_from_ledger_head != ledger_head:
            return ProjectionFreshness(
                name=self.name,
                exists=True,
                fresh=False,
                projection_head=descriptor.built_from_ledger_head,
                ledger_head=ledger_head,
                reason="projection_stale",
                logical_state_hash=descriptor.logical_state_hash,
            )
        return ProjectionFreshness(
            name=self.name,
            exists=True,
            fresh=True,
            projection_head=descriptor.built_from_ledger_head,
            ledger_head=ledger_head,
            reason="ok",
            logical_state_hash=descriptor.logical_state_hash,
        )


__all__ = [
    "FTS_BUILDER_VERSION",
    "FTS_PROJECTION_NAME",
    "FTS_PROJECTION_SCHEMA",
    "FtsProjectionAdapter",
]
