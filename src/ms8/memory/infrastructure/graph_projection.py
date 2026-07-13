"""Deterministic graph projection derived from authoritative replay state."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..application.replay import ClaimReplayView, ReplayState
from ..application.temporal_query import effective_valid_until
from ..domain.ledger import canonical_json
from ..ports.projection import ProjectionBuildResult, ProjectionDescriptor, ProjectionFreshness
from .projection_io import atomic_write_json, read_json_object, sha256_bytes

GRAPH_PROJECTION_NAME = "graph"
GRAPH_PROJECTION_SCHEMA = "ms8.graph_projection.v1"
GRAPH_BUILDER_VERSION = "2"


def _node(node_id: str, node_type: str, **attributes: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "attributes": attributes}


def _edge(source: str, target: str, relation: str, **attributes: Any) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "attributes": attributes,
    }


def _content_hash(nodes: object, edges: object) -> str:
    data = canonical_json({"nodes": nodes, "edges": edges}).encode("utf-8")
    return sha256_bytes(data)


def _latest_action(state: ReplayState, view: ClaimReplayView) -> str | None:
    if not view.decision_ids:
        return None
    decision = state.decisions.get(view.decision_ids[-1])
    return decision.action if decision is not None else None


def _forgotten_claim_ids(state: ReplayState) -> set[str]:
    return {
        claim_id
        for claim_id, view in state.claims.items()
        if _latest_action(state, view) == "forget"
    }


def _visible_event_ids(state: ReplayState, forgotten: set[str]) -> set[str]:
    claims_by_event: dict[str, set[str]] = {}
    for claim_id, view in state.claims.items():
        claims_by_event.setdefault(view.claim.created_from_event_id, set()).add(claim_id)
    visible: set[str] = set()
    for event_id in state.memory_events:
        related = claims_by_event.get(event_id, set())
        if not related or any(claim_id not in forgotten for claim_id in related):
            visible.add(event_id)
    return visible


def _decision_claim_ids(decision: Any) -> set[str]:
    claim_ids = {str(value) for value in decision.target_claim_ids}
    if decision.result_claim_id is not None:
        claim_ids.add(decision.result_claim_id)
    return claim_ids


def _graph_payload(state: ReplayState) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    forgotten = _forgotten_claim_ids(state)
    visible_events = _visible_event_ids(state, forgotten)

    for event_id in sorted(visible_events):
        event = state.memory_events[event_id]
        nodes.append(
            _node(
                event_id,
                "memory_event",
                kind=event.kind,
                observed_at=event.observed_at,
                observed_at_precision=event.observed_at_precision,
                trust_class=event.trust_class,
            )
        )

    for claim_id in sorted(state.claims):
        view = state.claims[claim_id]
        claim = view.claim
        if claim_id in forgotten:
            nodes.append(
                _node(
                    claim_id,
                    "claim_tombstone",
                    realm_id=claim.realm_id,
                    current_status=view.current_status,
                    decision_id=view.decision_ids[-1],
                )
            )
            continue
        nodes.append(
            _node(
                claim_id,
                "claim",
                kind=claim.kind,
                subject=claim.subject,
                predicate=claim.predicate,
                scope=claim.scope,
                realm_id=claim.realm_id,
                authority=claim.authority,
                sensitivity=claim.sensitivity,
                confidence=claim.confidence,
                proposed_status=claim.status,
                current_status=view.current_status,
                valid_time={
                    "start": claim.valid_time.start,
                    "end": effective_valid_until(state, view),
                    "basis": claim.valid_time.basis,
                },
            )
        )
        if claim.created_from_event_id in visible_events:
            edges.append(_edge(claim.created_from_event_id, claim_id, "created_claim"))

    for evidence_id in sorted(state.evidence):
        evidence = state.evidence[evidence_id]
        if evidence.claim_id in forgotten:
            continue
        nodes.append(
            _node(
                evidence_id,
                "evidence",
                relation=evidence.relation,
                weight=evidence.weight,
                quoted_text_hash=evidence.quoted_text_hash,
                fragment=evidence.to_dict()["fragment"],
            )
        )
        if evidence.event_id in visible_events:
            edges.append(_edge(evidence.event_id, evidence_id, "source_evidence"))
        edges.append(_edge(evidence_id, evidence.claim_id, evidence.relation))

    for decision_id in sorted(state.decisions):
        decision = state.decisions[decision_id]
        related_claim_ids = _decision_claim_ids(decision)
        if decision.action == "forget":
            nodes.append(
                _node(
                    decision_id,
                    "decision_tombstone",
                    action="forget",
                    recorded_at=decision.recorded_at,
                )
            )
            for claim_id in sorted(related_claim_ids & forgotten):
                edges.append(_edge(decision_id, claim_id, "forgets_claim"))
            continue
        if related_claim_ids and related_claim_ids <= forgotten:
            continue
        nodes.append(
            _node(
                decision_id,
                "decision",
                action=decision.action,
                result_status=decision.result_status,
                actor=decision.actor.to_dict(),
                reason=decision.reason,
                recorded_at=decision.recorded_at,
                policy=decision.to_dict()["policy"],
            )
        )
        for claim_id in sorted(decision.target_claim_ids):
            if claim_id not in forgotten:
                edges.append(_edge(decision_id, claim_id, "targets_claim"))
        if decision.result_claim_id is not None and decision.result_claim_id not in forgotten:
            edges.append(_edge(decision_id, decision.result_claim_id, "results_in_claim"))

    for conflict_id in sorted(state.conflicts):
        conflict = state.conflicts[conflict_id]
        claim_ids = conflict.get("claim_ids")
        if not isinstance(claim_ids, (list, tuple)):
            raise TypeError("conflict claim_ids must be a list or tuple")
        visible_claim_ids = sorted(
            str(value) for value in claim_ids if str(value) not in forgotten
        )
        if len(visible_claim_ids) < 2:
            continue
        nodes.append(
            _node(
                conflict_id,
                "conflict",
                claim_ids=visible_claim_ids,
                reason=str(conflict.get("reason") or ""),
            )
        )
        for claim_id in visible_claim_ids:
            edges.append(_edge(conflict_id, claim_id, "involves_claim"))

    nodes.sort(key=lambda item: (str(item["type"]), str(item["id"])))
    edges.sort(
        key=lambda item: (
            str(item["source"]),
            str(item["relation"]),
            str(item["target"]),
        )
    )
    return nodes, edges


class GraphProjectionAdapter:
    """Build a portable node/edge graph artifact from replay state."""

    def __init__(self, artifact_path: Path):
        self.artifact_path = Path(artifact_path)

    @property
    def name(self) -> str:
        return GRAPH_PROJECTION_NAME

    def rebuild_from_state(self, source: ReplayState) -> ProjectionBuildResult:
        nodes, edges = _graph_payload(source)
        payload = {
            "manifest": {
                "name": self.name,
                "schema": GRAPH_PROJECTION_SCHEMA,
                "builder_version": GRAPH_BUILDER_VERSION,
                "built_from_ledger_head": source.ledger_head,
                "last_sequence": source.last_sequence,
                "logical_state_hash": source.logical_state_hash,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "content_hash": _content_hash(nodes, edges),
            },
            "nodes": nodes,
            "edges": edges,
        }
        replaced = self.artifact_path.exists()
        artifact_hash = atomic_write_json(self.artifact_path, payload)
        return ProjectionBuildResult(
            descriptor=ProjectionDescriptor(
                name=self.name,
                schema=GRAPH_PROJECTION_SCHEMA,
                artifact_path=self.artifact_path,
                built_from_ledger_head=source.ledger_head,
                last_sequence=source.last_sequence,
                logical_state_hash=source.logical_state_hash,
                builder_version=GRAPH_BUILDER_VERSION,
                artifact_hash=artifact_hash,
            ),
            replaced_existing=replaced,
        )

    def read_descriptor(self) -> ProjectionDescriptor | None:
        payload = read_json_object(self.artifact_path)
        manifest = payload.get("manifest") if isinstance(payload, dict) else None
        nodes = payload.get("nodes") if isinstance(payload, dict) else None
        edges = payload.get("edges") if isinstance(payload, dict) else None
        if not isinstance(manifest, Mapping) or not isinstance(nodes, list) or not isinstance(edges, list):
            return None
        if manifest.get("content_hash") != _content_hash(nodes, edges):
            return None
        if manifest.get("node_count") != len(nodes) or manifest.get("edge_count") != len(edges):
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
        if descriptor.schema != GRAPH_PROJECTION_SCHEMA or descriptor.name != self.name:
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
    "GRAPH_BUILDER_VERSION",
    "GRAPH_PROJECTION_NAME",
    "GRAPH_PROJECTION_SCHEMA",
    "GraphProjectionAdapter",
]
