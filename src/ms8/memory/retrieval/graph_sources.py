"""Eligibility-restricted local graph expansion for Hybrid Retrieval v1."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..infrastructure.graph_projection import (
    GRAPH_PROJECTION_SCHEMA,
    GraphProjectionAdapter,
)
from ..infrastructure.projection_io import read_json_object
from .adapters import CandidateRecord, ProjectionCandidateSource
from .analyzer import analyze_query
from .models import RetrievalPlan

_ALLOWED_NODE_TYPES = frozenset({"claim", "memory_event", "evidence", "decision", "conflict"})


class GraphProjectionFormatError(RuntimeError):
    """Raised when a graph projection cannot safely provide candidates."""


@dataclass(frozen=True, slots=True)
class _GraphNode:
    node_id: str
    node_type: str
    attributes: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _GraphStep:
    source: str
    relation: str
    target: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "relation": self.relation,
            "target": self.target,
        }


def _required_mapping(value: object, message: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GraphProjectionFormatError(message)
    return value


def _required_sequence(value: object, message: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise GraphProjectionFormatError(message)
    return value


def _required_text(value: object, message: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise GraphProjectionFormatError(message)
    return text


def _query_terms(plan: RetrievalPlan) -> frozenset[str]:
    analysis = analyze_query(plan.query.text)
    terms = {
        str(value).casefold().strip()
        for value in (
            *analysis.tokens,
            *analysis.exact_tokens,
            *plan.entity_mentions,
        )
        if str(value).strip()
    }
    return frozenset(terms)


def _node_terms(node: _GraphNode) -> frozenset[str]:
    if node.node_type != "claim":
        return frozenset()
    values = (
        node.attributes.get("subject"),
        node.attributes.get("predicate"),
        node.attributes.get("kind"),
    )
    terms: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            terms.update(analyze_query(text).tokens)
        except ValueError:
            continue
        terms.add(text.casefold())
    return frozenset(term.casefold() for term in terms if term)


def _load_safe_graph(
    artifact_path: Path,
    eligible_claim_ids: tuple[str, ...],
) -> tuple[dict[str, _GraphNode], dict[str, tuple[tuple[str, str], ...]]]:
    descriptor = GraphProjectionAdapter(artifact_path).read_descriptor()
    if descriptor is None or descriptor.schema != GRAPH_PROJECTION_SCHEMA:
        raise GraphProjectionFormatError("graph projection is missing, invalid, or has the wrong schema")

    payload = _required_mapping(
        read_json_object(artifact_path),
        "graph projection is missing or unreadable",
    )
    raw_nodes = _required_sequence(payload.get("nodes"), "graph projection nodes are invalid")
    raw_edges = _required_sequence(payload.get("edges"), "graph projection edges are invalid")
    eligible = frozenset(eligible_claim_ids)

    nodes: dict[str, _GraphNode] = {}
    all_node_types: dict[str, str] = {}
    for raw_node in raw_nodes:
        node = _required_mapping(raw_node, "graph projection contains an invalid node")
        node_id = _required_text(node.get("id"), "graph node id must not be empty")
        node_type = _required_text(node.get("type"), "graph node type must not be empty")
        if node_id in all_node_types:
            raise GraphProjectionFormatError("graph projection contains duplicate node identifiers")
        all_node_types[node_id] = node_type
        if node_type not in _ALLOWED_NODE_TYPES:
            continue
        if node_type == "claim" and node_id not in eligible:
            continue
        attributes = _required_mapping(
            node.get("attributes", {}),
            f"graph node attributes are invalid: {node_id}",
        )
        nodes[node_id] = _GraphNode(
            node_id=node_id,
            node_type=node_type,
            attributes=attributes if node_type == "claim" else {},
        )

    adjacency_lists: dict[str, list[tuple[str, str]]] = {node_id: [] for node_id in nodes}
    seen_edges: set[tuple[str, str, str]] = set()
    for raw_edge in raw_edges:
        edge = _required_mapping(raw_edge, "graph projection contains an invalid edge")
        source = _required_text(edge.get("source"), "graph edge source must not be empty")
        target = _required_text(edge.get("target"), "graph edge target must not be empty")
        relation = _required_text(edge.get("relation"), "graph edge relation must not be empty")
        edge_key = (source, relation, target)
        if edge_key in seen_edges:
            raise GraphProjectionFormatError("graph projection contains duplicate edges")
        seen_edges.add(edge_key)
        if source not in all_node_types or target not in all_node_types:
            raise GraphProjectionFormatError("graph edge references a missing node")
        if source not in nodes or target not in nodes:
            continue
        adjacency_lists[source].append((target, relation))
        adjacency_lists[target].append((source, relation))

    adjacency = {
        node_id: tuple(sorted(neighbors, key=lambda item: (item[0], item[1])))
        for node_id, neighbors in adjacency_lists.items()
    }
    return nodes, adjacency


def _seed_claims(
    nodes: Mapping[str, _GraphNode],
    query_terms: frozenset[str],
) -> tuple[str, ...]:
    seeds: list[tuple[int, str]] = []
    for node_id, node in nodes.items():
        if node.node_type != "claim":
            continue
        overlap = query_terms.intersection(_node_terms(node))
        if overlap:
            seeds.append((-len(overlap), node_id))
    seeds.sort()
    return tuple(node_id for _overlap, node_id in seeds)


def _expand_paths(
    *,
    seeds: Sequence[str],
    nodes: Mapping[str, _GraphNode],
    adjacency: Mapping[str, tuple[tuple[str, str], ...]],
    max_hops: int,
) -> Mapping[str, tuple[_GraphStep, ...]]:
    best_paths: dict[str, tuple[_GraphStep, ...]] = {}
    seed_set = frozenset(seeds)
    for seed in seeds:
        queue: deque[tuple[str, tuple[_GraphStep, ...]]] = deque([(seed, ())])
        visited_depth: dict[str, int] = {seed: 0}
        while queue:
            current, path = queue.popleft()
            if len(path) >= max_hops:
                continue
            for neighbor, relation in adjacency.get(current, ()):
                next_path = (*path, _GraphStep(current, relation, neighbor))
                depth = len(next_path)
                previous_depth = visited_depth.get(neighbor)
                if previous_depth is not None and previous_depth < depth:
                    continue
                visited_depth[neighbor] = depth
                neighbor_node = nodes[neighbor]
                if neighbor_node.node_type == "claim" and neighbor not in seed_set:
                    previous = best_paths.get(neighbor)
                    candidate_key = (
                        depth,
                        tuple((step.source, step.relation, step.target) for step in next_path),
                    )
                    if previous is None:
                        best_paths[neighbor] = next_path
                    else:
                        previous_key = (
                            len(previous),
                            tuple((step.source, step.relation, step.target) for step in previous),
                        )
                        if candidate_key < previous_key:
                            best_paths[neighbor] = next_path
                if neighbor_node.node_type != "claim" or neighbor == seed:
                    queue.append((neighbor, next_path))
    return best_paths


class GraphProjectionCandidateProvider:
    """Expand one or two local graph hops without crossing eligibility."""

    def __init__(
        self,
        artifact_path: Path,
        evidence_resolver: Callable[[str], Sequence[str]],
        *,
        max_hops: int = 2,
    ) -> None:
        if isinstance(max_hops, bool) or not isinstance(max_hops, int) or max_hops not in {1, 2}:
            raise ValueError("graph max_hops must be 1 or 2")
        self.artifact_path = Path(artifact_path)
        self.evidence_resolver = evidence_resolver
        self.max_hops = max_hops

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

        nodes, adjacency = _load_safe_graph(self.artifact_path, eligible_claim_ids)
        query_terms = _query_terms(plan)
        seeds = _seed_claims(nodes, query_terms)
        if not seeds:
            return ()
        paths = _expand_paths(
            seeds=seeds,
            nodes=nodes,
            adjacency=adjacency,
            max_hops=self.max_hops,
        )

        records: list[CandidateRecord] = []
        for claim_id, path in paths.items():
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
            hop_count = len(path)
            records.append(
                CandidateRecord(
                    claim_id=claim_id,
                    evidence_ids=evidence_ids,
                    score=round(1.0 / (1.0 + hop_count), 12),
                    reason={
                        "projection_schema": GRAPH_PROJECTION_SCHEMA,
                        "hop_count": hop_count,
                        "path": tuple(step.to_dict() for step in path),
                        "seed_claim_id": path[0].source,
                    },
                )
            )
        records.sort(key=lambda item: (-item.score, item.claim_id, item.evidence_ids))
        return tuple(records[:limit])


class GraphProjectionCandidateSource(ProjectionCandidateSource):
    """Graph-channel adapter for eligibility-restricted local expansion."""

    def __init__(self, provider: GraphProjectionCandidateProvider) -> None:
        super().__init__(name="graph-projection", channel="graph", provider=provider)


__all__ = [
    "GraphProjectionCandidateProvider",
    "GraphProjectionCandidateSource",
    "GraphProjectionFormatError",
]
