"""Deterministic inverted-index projection derived from replay state."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..application.replay import ClaimReplayView, ReplayState
from ..application.temporal_query import effective_valid_until
from ..domain.ledger import canonical_json
from ..ports.projection import (
    ProjectionBuildResult,
    ProjectionDescriptor,
    ProjectionFreshness,
)
from .projection_io import atomic_write_json, read_json_object, sha256_bytes

SEARCH_PROJECTION_NAME = "search"
SEARCH_PROJECTION_SCHEMA = "ms8.search_projection.v1"
SEARCH_BUILDER_VERSION = "3"
_WORD_PATTERN = re.compile(r"[a-z0-9_]+")
_CJK_RUN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_FLAG_PATTERN = re.compile(r"(?<![A-Za-z0-9_])--?[A-Za-z0-9][A-Za-z0-9_-]*")
_VERSION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?",
    re.IGNORECASE,
)
_CALL_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.:<>-]*\(\)")
_SYMBOL_PATTERN = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.)[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?"
)
_QUOTED_PATH_PATTERN = re.compile(
    r"(?P<quote>['\"])(?P<path>(?:[A-Za-z]:\\|\.\.?/|/)[^'\"\r\n]+?)(?P=quote)"
)
_PATH_STOP_CHARS = r",;:()\[\]{}<>\"'，。；：（）【】《》“”‘’"
_PATH_PATTERN = re.compile(
    rf"(?:[A-Za-z]:\\(?:[^\\\s{_PATH_STOP_CHARS}]+\\)*"
    rf"[^\\\s{_PATH_STOP_CHARS}]+|(?:\.\.?/|/)[^\s{_PATH_STOP_CHARS}]+)"
)
_COMMAND_PATTERN = re.compile(
    r"(?:^|[\n`])\s*(?:\$\s*)?"
    r"((?:ms8|python(?:3)?|pip(?:3)?|git|pytest|ruff|mypy)\b[^\n`]{0,240})",
    re.IGNORECASE | re.MULTILINE,
)


def _unique(values: Sequence[object]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _terms(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    terms = set(_WORD_PATTERN.findall(normalized))
    for run in _CJK_RUN_PATTERN.findall(normalized):
        terms.update(run)
        if len(run) > 1:
            terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return tuple(sorted(term for term in terms if term))


def _content_hash(documents: object, postings: object) -> str:
    data = canonical_json({"documents": documents, "postings": postings}).encode("utf-8")
    return sha256_bytes(data)


def _latest_action(state: ReplayState, view: ClaimReplayView) -> str | None:
    if not view.decision_ids:
        return None
    decision = state.decisions.get(view.decision_ids[-1])
    return decision.action if decision is not None else None


def _value_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return canonical_json(value)


def _alias_values(value: object) -> tuple[str, ...]:
    aliases: list[object] = []
    if isinstance(value, str):
        aliases.append(value)
    if isinstance(value, Mapping):
        for field_name in ("alias", "aliases", "aka", "name", "names"):
            raw = value.get(field_name)
            if isinstance(raw, str):
                aliases.append(raw)
            elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
                aliases.extend(raw)
    return _unique(aliases)


def _compact_evidence(
    state: ReplayState,
    claim_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    evidence_ids: list[str] = []
    compact_text: list[str] = []
    aliases: list[str] = []
    for evidence_id in sorted(state.evidence):
        evidence = state.evidence[evidence_id]
        if evidence.claim_id != claim_id:
            continue
        evidence_ids.append(evidence_id)
        aliases.extend(_alias_values(evidence.fragment))
        if len(compact_text) >= 8:
            continue
        rendered = canonical_json(
            {
                "relation": evidence.relation,
                "fragment": evidence.fragment,
            }
        )
        compact_text.append(rendered if len(rendered) <= 320 else rendered[:317] + "...")
    return tuple(evidence_ids), tuple(compact_text), _unique(aliases)


def _exact_fields(value: str) -> dict[str, tuple[str, ...]]:
    quoted_paths = [match.group("path") for match in _QUOTED_PATH_PATTERN.finditer(value)]
    paths = _unique([*quoted_paths, *(match.group(0) for match in _PATH_PATTERN.finditer(value))])
    versions = _unique(match.group(0) for match in _VERSION_PATTERN.finditer(value))
    flags = _unique(match.group(0) for match in _FLAG_PATTERN.finditer(value))
    calls = _unique(match.group(0) for match in _CALL_PATTERN.finditer(value))
    commands = _unique(match.group(1).strip() for match in _COMMAND_PATTERN.finditer(value))

    symbols: list[str] = list(calls)
    for match in _SYMBOL_PATTERN.finditer(value):
        token = match.group(0)
        if (
            token.endswith("()")
            or "_" in token
            or "." in token
            or "::" in token
            or any(character.isupper() for character in token[1:])
        ):
            symbols.append(token)
    return {
        "code_symbols": _unique(symbols),
        "paths": paths,
        "versions": versions,
        "flags": flags,
        "commands": commands,
    }


def _document_payload(state: ReplayState, claim_id: str) -> dict[str, Any] | None:
    view = state.claims[claim_id]
    latest_action = _latest_action(state, view)
    if latest_action == "forget":
        return None
    claim = view.claim
    value_text = _value_text(claim.value)
    evidence_ids, evidence_text, evidence_aliases = _compact_evidence(state, claim_id)
    aliases = _unique([*_alias_values(claim.value), *evidence_aliases])
    exact_source = "\n".join((claim.text, value_text, *evidence_text))
    exact_fields = _exact_fields(exact_source)
    exact_terms = _unique(
        [
            *exact_fields["code_symbols"],
            *exact_fields["paths"],
            *exact_fields["versions"],
            *exact_fields["flags"],
            *exact_fields["commands"],
        ]
    )
    searchable = " ".join(
        (
            claim.text,
            claim.subject,
            claim.predicate,
            value_text,
            *aliases,
            *exact_terms,
            *evidence_text,
            claim.scope,
            claim.realm_id,
            claim.authority,
            claim.sensitivity,
            view.current_status,
        )
    )
    terms = set(_terms(searchable))
    terms.update(unicodedata.normalize("NFKC", token).casefold() for token in exact_terms)
    return {
        "claim_id": claim.claim_id,
        "text": claim.text,
        "subject": claim.subject,
        "predicate": claim.predicate,
        "value_text": value_text,
        "aliases": list(aliases),
        "code_symbols": list(exact_fields["code_symbols"]),
        "paths": list(exact_fields["paths"]),
        "versions": list(exact_fields["versions"]),
        "flags": list(exact_fields["flags"]),
        "commands": list(exact_fields["commands"]),
        "evidence_ids": list(evidence_ids),
        "evidence_text": list(evidence_text),
        "scope": claim.scope,
        "realm_id": claim.realm_id,
        "authority": claim.authority,
        "sensitivity": claim.sensitivity,
        "confidence": claim.confidence,
        "proposed_status": claim.status,
        "current_status": view.current_status,
        "lifecycle": {
            "proposed_status": claim.status,
            "current_status": view.current_status,
            "latest_action": latest_action,
        },
        "valid_time": {
            "start": claim.valid_time.start,
            "end": effective_valid_until(state, view),
            "basis": claim.valid_time.basis,
        },
        "decision_ids": list(view.decision_ids),
        "terms": sorted(term for term in terms if term),
    }


class SearchProjectionAdapter:
    """Build a portable JSON inverted index for later retrieval adapters."""

    def __init__(self, artifact_path: Path):
        self.artifact_path = Path(artifact_path)

    @property
    def name(self) -> str:
        return SEARCH_PROJECTION_NAME

    def rebuild_from_state(self, source: ReplayState) -> ProjectionBuildResult:
        documents: list[dict[str, Any]] = []
        for claim_id in sorted(source.claims):
            document = _document_payload(source, claim_id)
            if document is not None:
                documents.append(document)
        postings: dict[str, list[str]] = {}
        for document in documents:
            claim_id = str(document["claim_id"])
            raw_terms = document["terms"]
            if not isinstance(raw_terms, list):
                raise TypeError("search document terms must be a list")
            for term in raw_terms:
                postings.setdefault(str(term), []).append(claim_id)
        for claim_ids in postings.values():
            claim_ids.sort()
        ordered_postings = {term: postings[term] for term in sorted(postings)}
        payload = {
            "manifest": {
                "name": self.name,
                "schema": SEARCH_PROJECTION_SCHEMA,
                "builder_version": SEARCH_BUILDER_VERSION,
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
                schema=SEARCH_PROJECTION_SCHEMA,
                artifact_path=self.artifact_path,
                built_from_ledger_head=source.ledger_head,
                last_sequence=source.last_sequence,
                logical_state_hash=source.logical_state_hash,
                builder_version=SEARCH_BUILDER_VERSION,
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
        if descriptor.schema != SEARCH_PROJECTION_SCHEMA or descriptor.name != self.name:
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
    "SEARCH_BUILDER_VERSION",
    "SEARCH_PROJECTION_NAME",
    "SEARCH_PROJECTION_SCHEMA",
    "SearchProjectionAdapter",
]
