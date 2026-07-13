"""Deterministic local vector projection derived from authoritative replay state.

The projection intentionally avoids external embedding services. Terms are mapped
into a fixed-size signed hashing vector so the artifact is portable, repeatable,
and fully rebuildable from the ledger on every supported platform.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..application.replay import ClaimReplayView, ReplayState
from ..application.temporal_query import effective_valid_until
from ..domain.ledger import canonical_json
from ..ports.projection import ProjectionBuildResult, ProjectionDescriptor, ProjectionFreshness
from .projection_io import atomic_write_json, read_json_object, sha256_bytes
from .search_projection import _terms

VECTOR_PROJECTION_NAME = "vector"
VECTOR_PROJECTION_SCHEMA = "ms8.vector_projection.v1"
VECTOR_BUILDER_VERSION = "1"
VECTOR_DIMENSIONS = 64


def _latest_action(state: ReplayState, view: ClaimReplayView) -> str | None:
    if not view.decision_ids:
        return None
    decision = state.decisions.get(view.decision_ids[-1])
    return decision.action if decision is not None else None


def _vectorize(value: str) -> list[float]:
    vector = [0.0] * VECTOR_DIMENSIONS
    for term in _terms(value):
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % VECTOR_DIMENSIONS
        sign = -1.0 if digest[4] & 1 else 1.0
        vector[index] += sign
    norm = math.sqrt(sum(item * item for item in vector))
    if norm > 0.0:
        vector = [round(item / norm, 8) for item in vector]
    return vector


def _content_hash(vectors: object) -> str:
    return sha256_bytes(canonical_json({"vectors": vectors}).encode("utf-8"))


def _vector_document(state: ReplayState, claim_id: str) -> dict[str, Any] | None:
    view = state.claims[claim_id]
    if _latest_action(state, view) == "forget":
        return None
    claim = view.claim
    text = " ".join(
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
        "dimensions": VECTOR_DIMENSIONS,
        "vector": _vectorize(text),
        "current_status": view.current_status,
        "valid_from": claim.valid_time.start,
        "valid_until": effective_valid_until(state, view),
    }


class VectorProjectionAdapter:
    """Build a deterministic signed-hashing vector artifact from replay state."""

    def __init__(self, artifact_path: Path):
        self.artifact_path = Path(artifact_path)

    @property
    def name(self) -> str:
        return VECTOR_PROJECTION_NAME

    def rebuild_from_state(self, source: ReplayState) -> ProjectionBuildResult:
        vectors: list[dict[str, Any]] = []
        for claim_id in sorted(source.claims):
            document = _vector_document(source, claim_id)
            if document is not None:
                vectors.append(document)
        payload = {
            "manifest": {
                "name": self.name,
                "schema": VECTOR_PROJECTION_SCHEMA,
                "builder_version": VECTOR_BUILDER_VERSION,
                "built_from_ledger_head": source.ledger_head,
                "last_sequence": source.last_sequence,
                "logical_state_hash": source.logical_state_hash,
                "vector_count": len(vectors),
                "dimensions": VECTOR_DIMENSIONS,
                "content_hash": _content_hash(vectors),
            },
            "vectors": vectors,
        }
        replaced = self.artifact_path.exists()
        artifact_hash = atomic_write_json(self.artifact_path, payload)
        return ProjectionBuildResult(
            descriptor=ProjectionDescriptor(
                name=self.name,
                schema=VECTOR_PROJECTION_SCHEMA,
                artifact_path=self.artifact_path,
                built_from_ledger_head=source.ledger_head,
                last_sequence=source.last_sequence,
                logical_state_hash=source.logical_state_hash,
                builder_version=VECTOR_BUILDER_VERSION,
                artifact_hash=artifact_hash,
            ),
            replaced_existing=replaced,
        )

    def read_descriptor(self) -> ProjectionDescriptor | None:
        payload = read_json_object(self.artifact_path)
        manifest = payload.get("manifest") if isinstance(payload, dict) else None
        vectors = payload.get("vectors") if isinstance(payload, dict) else None
        if not isinstance(manifest, Mapping) or not isinstance(vectors, list):
            return None
        if manifest.get("content_hash") != _content_hash(vectors):
            return None
        if manifest.get("vector_count") != len(vectors):
            return None
        if manifest.get("dimensions") != VECTOR_DIMENSIONS:
            return None
        for item in vectors:
            if not isinstance(item, Mapping):
                return None
            raw = item.get("vector")
            if not isinstance(raw, list) or len(raw) != VECTOR_DIMENSIONS:
                return None
            if not all(isinstance(value, int | float) and math.isfinite(float(value)) for value in raw):
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
        if descriptor.schema != VECTOR_PROJECTION_SCHEMA or descriptor.name != self.name:
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
    "VECTOR_BUILDER_VERSION",
    "VECTOR_DIMENSIONS",
    "VECTOR_PROJECTION_NAME",
    "VECTOR_PROJECTION_SCHEMA",
    "VectorProjectionAdapter",
]
