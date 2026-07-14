"""Versioned local embedding projection artifact for Hybrid Retrieval v1.

The artifact is disposable and non-authoritative. Every entry is bound to both a
source ``content_hash`` and the manifest ``model_id`` so stale vectors can be
identified without mutating Ledger data.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..domain.ledger import canonical_json
from .projection_io import atomic_write_json, read_json_object, sha256_bytes

EMBEDDING_PROJECTION_NAME = "embedding"
EMBEDDING_PROJECTION_SCHEMA = "ms8.embedding_projection.v1"
EMBEDDING_BUILDER_VERSION = "1"


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_vector(values: Sequence[float], field_name: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{field_name} must be a numeric sequence")
    vector = tuple(float(value) for value in values)
    if not vector:
        raise ValueError(f"{field_name} must not be empty")
    if any(not math.isfinite(value) for value in vector):
        raise ValueError(f"{field_name} must contain only finite values")
    return vector


@dataclass(frozen=True, slots=True)
class EmbeddingProjectionEntry:
    claim_id: str
    content_hash: str
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _required_text(self.claim_id, "entry.claim_id"))
        object.__setattr__(
            self,
            "content_hash",
            _required_text(self.content_hash, "entry.content_hash"),
        )
        object.__setattr__(
            self,
            "vector",
            _normalize_vector(self.vector, f"entry.vector[{self.claim_id}]"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "content_hash": self.content_hash,
            "vector": list(self.vector),
        }


@dataclass(frozen=True, slots=True)
class EmbeddingProjectionSnapshot:
    model_id: str
    dimensions: int
    built_from_ledger_head: str
    last_sequence: int
    logical_state_hash: str
    entries: tuple[EmbeddingProjectionEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _required_text(self.model_id, "snapshot.model_id"))
        if isinstance(self.dimensions, bool) or not isinstance(self.dimensions, int) or self.dimensions < 1:
            raise ValueError("snapshot.dimensions must be a positive integer")
        object.__setattr__(
            self,
            "built_from_ledger_head",
            _required_text(self.built_from_ledger_head, "snapshot.built_from_ledger_head"),
        )
        if isinstance(self.last_sequence, bool) or not isinstance(self.last_sequence, int) or self.last_sequence < 0:
            raise ValueError("snapshot.last_sequence must be a non-negative integer")
        object.__setattr__(
            self,
            "logical_state_hash",
            _required_text(self.logical_state_hash, "snapshot.logical_state_hash"),
        )
        if any(not isinstance(entry, EmbeddingProjectionEntry) for entry in self.entries):
            raise TypeError("snapshot.entries must contain EmbeddingProjectionEntry values")
        ordered = tuple(sorted(self.entries, key=lambda entry: entry.claim_id))
        identifiers = tuple(entry.claim_id for entry in ordered)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("snapshot.entries must not contain duplicate claim identifiers")
        for entry in ordered:
            if len(entry.vector) != self.dimensions:
                raise ValueError(
                    f"embedding dimensions mismatch for {entry.claim_id}: "
                    f"expected={self.dimensions} actual={len(entry.vector)}"
                )
        object.__setattr__(self, "entries", ordered)

    @property
    def projection_content_hash(self) -> str:
        payload = [entry.to_dict() for entry in self.entries]
        return sha256_bytes(canonical_json({"embeddings": payload}).encode("utf-8"))

    @property
    def vectors(self) -> Mapping[str, tuple[float, ...]]:
        return MappingProxyType({entry.claim_id: entry.vector for entry in self.entries})

    @property
    def source_content_hashes(self) -> Mapping[str, str]:
        return MappingProxyType({entry.claim_id: entry.content_hash for entry in self.entries})

    def to_payload(self) -> dict[str, Any]:
        embeddings = [entry.to_dict() for entry in self.entries]
        return {
            "manifest": {
                "name": EMBEDDING_PROJECTION_NAME,
                "schema": EMBEDDING_PROJECTION_SCHEMA,
                "builder_version": EMBEDDING_BUILDER_VERSION,
                "model_id": self.model_id,
                "dimensions": self.dimensions,
                "built_from_ledger_head": self.built_from_ledger_head,
                "last_sequence": self.last_sequence,
                "logical_state_hash": self.logical_state_hash,
                "document_count": len(embeddings),
                "content_hash": self.projection_content_hash,
            },
            "embeddings": embeddings,
        }


def write_embedding_projection(path: Path, snapshot: EmbeddingProjectionSnapshot) -> str:
    if not isinstance(snapshot, EmbeddingProjectionSnapshot):
        raise TypeError("snapshot must be EmbeddingProjectionSnapshot")
    return atomic_write_json(Path(path), snapshot.to_payload())


def read_embedding_projection(path: Path) -> EmbeddingProjectionSnapshot | None:
    payload = read_json_object(Path(path))
    if not isinstance(payload, Mapping):
        return None
    manifest = payload.get("manifest")
    raw_entries = payload.get("embeddings")
    if not isinstance(manifest, Mapping) or not isinstance(raw_entries, list):
        return None
    raw_dimensions = manifest.get("dimensions")
    raw_last_sequence = manifest.get("last_sequence")
    if isinstance(raw_dimensions, bool) or not isinstance(raw_dimensions, int):
        return None
    if isinstance(raw_last_sequence, bool) or not isinstance(raw_last_sequence, int):
        return None
    try:
        if manifest.get("name") != EMBEDDING_PROJECTION_NAME:
            return None
        if manifest.get("schema") != EMBEDDING_PROJECTION_SCHEMA:
            return None
        if manifest.get("builder_version") != EMBEDDING_BUILDER_VERSION:
            return None
        entries: list[EmbeddingProjectionEntry] = []
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, Mapping):
                return None
            raw_vector = raw_entry.get("vector")
            if not isinstance(raw_vector, Sequence) or isinstance(raw_vector, (str, bytes, bytearray)):
                return None
            entries.append(
                EmbeddingProjectionEntry(
                    claim_id=str(raw_entry.get("claim_id") or ""),
                    content_hash=str(raw_entry.get("content_hash") or ""),
                    vector=tuple(float(value) for value in raw_vector),
                )
            )
        snapshot = EmbeddingProjectionSnapshot(
            model_id=str(manifest.get("model_id") or ""),
            dimensions=raw_dimensions,
            built_from_ledger_head=str(manifest.get("built_from_ledger_head") or ""),
            last_sequence=raw_last_sequence,
            logical_state_hash=str(manifest.get("logical_state_hash") or ""),
            entries=tuple(entries),
        )
    except (TypeError, ValueError):
        return None
    if manifest.get("document_count") != len(snapshot.entries):
        return None
    if manifest.get("content_hash") != snapshot.projection_content_hash:
        return None
    return snapshot


def embedding_projection_rebuild_reasons(
    snapshot: EmbeddingProjectionSnapshot,
    *,
    model_id: str,
    dimensions: int,
    content_hashes: Mapping[str, str],
) -> tuple[str, ...]:
    """Explain why a stored embedding projection must be rebuilt."""

    if not isinstance(snapshot, EmbeddingProjectionSnapshot):
        raise TypeError("snapshot must be EmbeddingProjectionSnapshot")
    expected_model = _required_text(model_id, "model_id")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
        raise ValueError("dimensions must be a positive integer")
    normalized_hashes = {
        _required_text(claim_id, "content_hashes claim_id"): _required_text(
            content_hash,
            f"content_hashes[{claim_id}]",
        )
        for claim_id, content_hash in content_hashes.items()
    }

    reasons: list[str] = []
    if snapshot.model_id != expected_model:
        reasons.append("model_id_mismatch")
    if snapshot.dimensions != dimensions:
        reasons.append("dimensions_mismatch")
    stored_hashes = dict(snapshot.source_content_hashes)
    if set(stored_hashes) != set(normalized_hashes):
        reasons.append("content_set_mismatch")
    elif any(stored_hashes[claim_id] != content_hash for claim_id, content_hash in normalized_hashes.items()):
        reasons.append("content_hash_mismatch")
    return tuple(reasons)


__all__ = [
    "EMBEDDING_BUILDER_VERSION",
    "EMBEDDING_PROJECTION_NAME",
    "EMBEDDING_PROJECTION_SCHEMA",
    "EmbeddingProjectionEntry",
    "EmbeddingProjectionSnapshot",
    "embedding_projection_rebuild_reasons",
    "read_embedding_projection",
    "write_embedding_projection",
]
