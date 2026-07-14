"""Explicit Ledger-v1 Hybrid Retrieval runtime integration.

This module composes the Phase 1-7 retrieval components without changing the
legacy runtime path.  Callers must select the ``hybrid-v1`` profile through the
Ledger compatibility gate before constructing this runtime.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from ..application.replay import ReplayState
from ..application.temporal_query import replay_recorded_as_of
from ..domain.ledger import LedgerTransaction
from ..infrastructure.embedding_projection import read_embedding_projection
from .adapters import (
    CandidateBatch,
    LedgerLexicalCandidateSource,
    run_candidate_sources,
)
from .candidate_sources import CandidateSource
from .context_assembly import MMRConfig, build_agent_context
from .eligibility import EligibilityEvaluation, EligibilityEvaluator, EligibleClaims
from .embedding_sources import (
    EmbeddingProjectionCandidateProvider,
    EmbeddingProjectionCandidateSource,
)
from .entity_sources import EntityProjectionCandidateProvider, EntityProjectionCandidateSource
from .fusion import FusionConfig, FusionResult, fuse_and_rerank
from .graph_sources import GraphProjectionCandidateProvider, GraphProjectionCandidateSource
from .models import (
    CandidateChannel,
    CandidateHit,
    MemoryQuery,
    Principal,
    RankedClaim,
    RetrievalPlan,
    RetrievalPurpose,
    TimeCoordinates,
)
from .ollama_embedding import OllamaEmbeddingProvider
from .projection_sources import SearchProjectionCandidateProvider
from .query_planner import QueryPlanner, QueryPlanningResult
from .temporal_sources import TemporalReplayCandidateProvider, TemporalReplayCandidateSource

HYBRID_RETRIEVAL_PROFILE = "hybrid-v1"
HYBRID_RETRIEVAL_ENV_FLAG = "MS8_MEMORY_HYBRID_V1"

_ALLOWED_PURPOSES = frozenset(
    {"recall", "prepare_reply", "inject", "historical", "review", "audit"}
)


def _required_positive_int(value: object, field_name: str, default: int) -> int:
    candidate = default if value is None else value
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return candidate


def _strict_bool(value: object, field_name: str, default: bool = False) -> bool:
    candidate = default if value is None else value
    if not isinstance(candidate, bool):
        raise TypeError(f"{field_name} must be a boolean")
    return candidate


def _required_text(value: object, field_name: str, default: str) -> str:
    text = str(value if value is not None else default).strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _text_tuple(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("hybrid principal sensitivities must be an array")
    normalized = tuple(str(item or "").strip() for item in value)
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError("hybrid principal sensitivities must contain unique non-empty values")
    return normalized


def _parse_time(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _reference_time(state: ReplayState) -> datetime:
    values: list[datetime] = []
    for event in state.memory_events.values():
        parsed = _parse_time(event.observed_at)
        if parsed is not None:
            values.append(parsed)
    for decision in state.decisions.values():
        parsed = _parse_time(decision.recorded_at)
        if parsed is not None:
            values.append(parsed)
    for view in state.claims.values():
        parsed = _parse_time(view.claim.valid_time.start)
        if parsed is not None:
            values.append(parsed)
    return max(values) if values else datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class HybridRuntimePaths:
    search_projection: Path
    graph_projection: Path
    embedding_projection: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "search_projection", Path(self.search_projection))
        object.__setattr__(self, "graph_projection", Path(self.graph_projection))
        object.__setattr__(self, "embedding_projection", Path(self.embedding_projection))


@dataclass(frozen=True, slots=True)
class HybridRuntimeConfig:
    timezone_name: str = "UTC"
    context_budget_tokens: int = 1200
    max_claims: int = 12
    max_per_subject: int = 3
    max_per_predicate: int = 3
    max_per_subject_predicate: int = 2
    max_fact_chars: int = 320
    graph_max_hops: int = 2
    principal_id: str = "local:ledger-v1"
    principal_kind: str = "user"
    principal_realm_ids: tuple[str, ...] = ("local",)
    principal_scopes: tuple[str, ...] = ()
    allowed_sensitivities: tuple[str, ...] = ("public", "internal", "private")
    embedding: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> HybridRuntimeConfig:
        section = raw if isinstance(raw, Mapping) else {}
        graph_max_hops = _required_positive_int(
            section.get("graph_max_hops"), "hybrid.graph_max_hops", 2
        )
        if graph_max_hops not in {1, 2}:
            raise ValueError("hybrid.graph_max_hops must be 1 or 2")
        principal_kind = _required_text(
            section.get("principal_kind"), "hybrid.principal_kind", "user"
        )
        if principal_kind not in {"user", "agent", "mcp_client", "system", "reviewer"}:
            raise ValueError(f"unsupported hybrid principal_kind: {principal_kind}")
        embedding = section.get("embedding")
        if embedding is not None and not isinstance(embedding, Mapping):
            raise TypeError("hybrid.embedding must be an object")
        principal_realm_ids = _text_tuple(section.get("principal_realm_ids"), ())
        if not principal_realm_ids:
            raise ValueError("hybrid.principal_realm_ids must contain at least one realm")
        principal_scopes = _text_tuple(section.get("principal_scopes"), ())
        if not principal_scopes:
            raise ValueError("hybrid.principal_scopes must contain at least one scope")
        return cls(
            timezone_name=_required_text(
                section.get("timezone"), "hybrid.timezone", "UTC"
            ),
            context_budget_tokens=_required_positive_int(
                section.get("context_budget_tokens"),
                "hybrid.context_budget_tokens",
                1200,
            ),
            max_claims=_required_positive_int(
                section.get("max_claims"), "hybrid.max_claims", 12
            ),
            max_per_subject=_required_positive_int(
                section.get("max_per_subject"), "hybrid.max_per_subject", 3
            ),
            max_per_predicate=_required_positive_int(
                section.get("max_per_predicate"), "hybrid.max_per_predicate", 3
            ),
            max_per_subject_predicate=_required_positive_int(
                section.get("max_per_subject_predicate"),
                "hybrid.max_per_subject_predicate",
                2,
            ),
            max_fact_chars=_required_positive_int(
                section.get("max_fact_chars"), "hybrid.max_fact_chars", 320
            ),
            graph_max_hops=graph_max_hops,
            principal_id=_required_text(
                section.get("principal_id"),
                "hybrid.principal_id",
                "local:ledger-v1",
            ),
            principal_kind=principal_kind,
            principal_realm_ids=principal_realm_ids,
            principal_scopes=principal_scopes,
            allowed_sensitivities=_text_tuple(
                section.get("allowed_sensitivities"),
                ("public", "internal", "private"),
            ),
            embedding=dict(embedding) if isinstance(embedding, Mapping) else None,
        )


class _UnavailableVectorSource:
    name = "embedding-unavailable"
    channel: CandidateChannel = "vector"

    def retrieve(
        self,
        _plan: RetrievalPlan,
        _eligible: EligibleClaims,
    ) -> tuple[CandidateHit, ...]:
        raise RuntimeError("embedding provider is not configured")


@dataclass(frozen=True, slots=True)
class HybridExecution:
    planning: QueryPlanningResult
    eligibility: EligibilityEvaluation
    candidates: CandidateBatch
    fusion: FusionResult


class HybridRetrievalRuntime:
    """Compose governed planning, eligibility, retrieval, fusion, and assembly."""

    def __init__(
        self,
        state: ReplayState,
        paths: HybridRuntimePaths,
        *,
        config: HybridRuntimeConfig | None = None,
        transactions: Sequence[LedgerTransaction] | None = None,
    ) -> None:
        if not isinstance(state, ReplayState):
            raise TypeError("state must be ReplayState")
        if not isinstance(paths, HybridRuntimePaths):
            raise TypeError("paths must be HybridRuntimePaths")
        self.state = state
        self.paths = paths
        self.config = config or HybridRuntimeConfig()
        if not isinstance(self.config, HybridRuntimeConfig):
            raise TypeError("config must be HybridRuntimeConfig")
        if transactions is not None and any(
            not isinstance(transaction, LedgerTransaction) for transaction in transactions
        ):
            raise TypeError("transactions must contain LedgerTransaction values")
        self._transactions = tuple(transactions) if transactions is not None else None
        self._planner = QueryPlanner(timezone_name=self.config.timezone_name)
        self._eligibility = EligibilityEvaluator()

    def _recorded_runtime(self, recorded_as_of: str | None) -> HybridRetrievalRuntime:
        if recorded_as_of is None or self._transactions is None:
            return self
        return HybridRetrievalRuntime(
            replay_recorded_as_of(self._transactions, recorded_as_of),
            self.paths,
            config=self.config,
        )

    def _evidence_ids(self, claim_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                evidence_id
                for evidence_id, evidence in self.state.evidence.items()
                if evidence.claim_id == claim_id
            )
        )

    def _principal(self, realm_id: str | None, scope: str | None) -> Principal:
        del realm_id, scope
        return Principal(
            principal_id=self.config.principal_id,
            kind=cast(Any, self.config.principal_kind),
            realm_ids=self.config.principal_realm_ids,
            scopes=self.config.principal_scopes,
            allowed_sensitivities=self.config.allowed_sensitivities,
            capabilities=tuple(sorted(_ALLOWED_PURPOSES)),
        )

    def _vector_source(self) -> CandidateSource:
        embedding = self.config.embedding
        if not isinstance(embedding, Mapping) or embedding.get("enabled") is not True:
            return _UnavailableVectorSource()
        provider_name = str(embedding.get("provider") or "ollama").strip().casefold()
        if provider_name != "ollama":
            raise ValueError(f"unsupported hybrid embedding provider: {provider_name}")
        provider = OllamaEmbeddingProvider(
            model=_required_text(embedding.get("model"), "hybrid.embedding.model", ""),
            dimensions=_required_positive_int(
                embedding.get("dimensions"), "hybrid.embedding.dimensions", 0
            ),
            host=_required_text(
                embedding.get("host"),
                "hybrid.embedding.host",
                "http://127.0.0.1:11434",
            ),
            timeout_seconds=float(embedding.get("timeout_seconds", 10.0)),
            allow_remote=_strict_bool(
                embedding.get("allow_remote"),
                "hybrid.embedding.allow_remote",
            ),
        )
        return EmbeddingProjectionCandidateSource(
            EmbeddingProjectionCandidateProvider(
                self.paths.embedding_projection,
                provider,
                self._evidence_ids,
                state=self.state,
            )
        )

    def _sources(self) -> tuple[CandidateSource, ...]:
        return (
            LedgerLexicalCandidateSource(
                SearchProjectionCandidateProvider(
                    self.paths.search_projection,
                    self._evidence_ids,
                )
            ),
            self._vector_source(),
            EntityProjectionCandidateSource(
                EntityProjectionCandidateProvider(
                    self.paths.search_projection,
                    self._evidence_ids,
                )
            ),
            TemporalReplayCandidateSource(TemporalReplayCandidateProvider(self.state)),
            GraphProjectionCandidateSource(
                GraphProjectionCandidateProvider(
                    self.paths.graph_projection,
                    self._evidence_ids,
                    max_hops=self.config.graph_max_hops,
                )
            ),
        )

    def execute(
        self,
        text: str,
        *,
        purpose: str = "recall",
        recorded_as_of: str | None = None,
        observed_as_of: str | None = None,
        valid_at: str | None = None,
        realm_id: str | None = None,
        scope: str | None = None,
    ) -> HybridExecution:
        recorded_runtime = self._recorded_runtime(recorded_as_of)
        if recorded_runtime is not self:
            return recorded_runtime.execute(
                text,
                purpose=purpose,
                recorded_as_of=recorded_as_of,
                observed_as_of=observed_as_of,
                valid_at=valid_at,
                realm_id=realm_id,
                scope=scope,
            )
        normalized_purpose = str(purpose or "").strip()
        if normalized_purpose not in _ALLOWED_PURPOSES:
            raise ValueError(f"unsupported hybrid retrieval purpose: {normalized_purpose}")
        query = MemoryQuery(
            text=text,
            purpose=cast(RetrievalPurpose, normalized_purpose),
            time=TimeCoordinates(
                recorded_as_of=recorded_as_of,
                observed_as_of=observed_as_of,
                valid_at=valid_at,
            ),
            realm_ids=(realm_id,) if realm_id else (),
            scope=scope,
        )
        planning = self._planner.plan(
            query,
            self._principal(realm_id, scope),
            reference_time=_reference_time(self.state),
            context_budget_tokens=self.config.context_budget_tokens,
        )
        eligibility = self._eligibility.evaluate(self.state, planning.plan)
        candidates = run_candidate_sources(
            self._sources(),
            planning.plan,
            eligibility.eligible,
        )
        fusion = fuse_and_rerank(
            candidates,
            self.state,
            planning.plan,
            eligibility.eligible,
            config=FusionConfig(),
        )
        return HybridExecution(
            planning=planning,
            eligibility=eligibility,
            candidates=candidates,
            fusion=fusion,
        )

    def _governance(self, claim_id: str) -> dict[str, bool]:
        values = {"can_recall": True, "can_inject": False, "can_act_on": False}
        view = self.state.claims[claim_id]
        for decision_id in view.decision_ids:
            decision = self.state.decisions.get(decision_id)
            configured = decision.policy.get("governance") if decision is not None else None
            if not isinstance(configured, Mapping):
                continue
            for key in tuple(values):
                raw = configured.get(key)
                if isinstance(raw, bool):
                    values[key] = raw
        return values

    def _conflict_ids(self, claim_id: str) -> tuple[str, ...]:
        result: list[str] = []
        for conflict_id, payload in sorted(self.state.conflicts.items()):
            raw_ids = payload.get("claim_ids", ())
            if isinstance(raw_ids, Sequence) and not isinstance(
                raw_ids, (str, bytes, bytearray)
            ):
                if claim_id in {str(value) for value in raw_ids}:
                    result.append(conflict_id)
        return tuple(result)

    def _matched_terms(self, execution: HybridExecution, claim_id: str) -> tuple[str, ...]:
        values: set[str] = set()
        for hits in execution.candidates.hits_by_source.values():
            for hit in hits:
                if hit.claim_id != claim_id:
                    continue
                raw = hit.reason.get("matched_terms", ())
                if isinstance(raw, Sequence) and not isinstance(
                    raw, (str, bytes, bytearray)
                ):
                    values.update(str(item) for item in raw if str(item).strip())
                entity = str(hit.reason.get("matched_entity") or "").strip()
                if entity:
                    values.add(entity)
        return tuple(sorted(values))

    def _row(self, execution: HybridExecution, ranked: RankedClaim) -> dict[str, object]:
        view = self.state.claims[ranked.claim_id]
        claim = view.claim
        governance = self._governance(ranked.claim_id)
        conflict_ids = self._conflict_ids(ranked.claim_id)
        return {
            "id": ranked.claim_id,
            "text": claim.text,
            "normalized_text": claim.text,
            "category": claim.predicate,
            "status": view.current_status,
            "source": claim.created_from_event_id,
            "score": ranked.score,
            "scope": claim.scope,
            "realm_id": claim.realm_id,
            "authority": claim.authority,
            "sensitivity": claim.sensitivity,
            "confidence": claim.confidence,
            **governance,
            "provenance": {
                "claim_id": ranked.claim_id,
                "source_event_id": claim.created_from_event_id,
                "evidence_ids": list(ranked.evidence_ids),
                "decision_ids": list(view.decision_ids),
                "conflict_ids": list(conflict_ids),
            },
            "conflicts": [
                {"conflict_id": conflict_id, "status": "unresolved"}
                for conflict_id in conflict_ids
            ],
            "ranking_explanation": list(ranked.explanation),
            "matched_terms": list(self._matched_terms(execution, ranked.claim_id)),
        }

    @staticmethod
    def _ranked_dict(ranked: RankedClaim) -> dict[str, object]:
        return {
            "claim_id": ranked.claim_id,
            "evidence_ids": list(ranked.evidence_ids),
            "score": ranked.score,
            "hard_rule_tier": ranked.hard_rule_tier,
            "score_components": dict(ranked.score_components),
            "explanation": list(ranked.explanation),
        }

    def _trace(self, execution: HybridExecution, *, full: bool) -> dict[str, object]:
        eligible = execution.eligibility.eligible
        source_summary = [
            {
                "source": trace.source,
                "channel": trace.channel,
                "status": trace.status,
                "hit_count": trace.hit_count,
                "degradation_reason": trace.degradation_reason,
            }
            for trace in execution.candidates.traces
        ]
        payload: dict[str, object] = {
            "profile": HYBRID_RETRIEVAL_PROFILE,
            "eligibility": {
                "evaluated_count": eligible.evaluated_count,
                "eligible_count": len(eligible.claim_ids),
                "blocked_reasons": dict(eligible.blocked_reasons),
                "authority_aliases": dict(execution.eligibility.authority_aliases),
            },
            "sources": source_summary,
            "fusion": {
                "config_schema": execution.fusion.config_schema,
                "candidate_count": execution.fusion.candidate_count,
                "source_names": list(execution.fusion.source_names),
            },
        }
        if full:
            payload["plan"] = execution.planning.to_dict()
            payload["eligibility"] = {
                **cast(dict[str, object], payload["eligibility"]),
                "eligible_claim_ids": list(eligible.claim_ids),
            }
            payload["source_hits"] = {
                source: [
                    {
                        "claim_id": hit.claim_id,
                        "evidence_ids": list(hit.evidence_ids),
                        "channel": hit.channel,
                        "rank": hit.rank,
                        "raw_score": hit.raw_score,
                        "reason": dict(hit.reason),
                    }
                    for hit in hits
                ]
                for source, hits in execution.candidates.hits_by_source.items()
            }
            payload["reranking"] = {
                "ranked": [
                    self._ranked_dict(item) for item in execution.fusion.ranked_claims
                ],
            }
        return payload

    def gateway_trace(self, execution: HybridExecution) -> dict[str, object]:
        eligible = execution.eligibility.eligible
        return {
            "provider": "ledger-v1",
            "candidate_source": HYBRID_RETRIEVAL_PROFILE,
            "retrieval_profile": HYBRID_RETRIEVAL_PROFILE,
            "ledger_head": self.state.ledger_head,
            "last_sequence": self.state.last_sequence,
            "policy_filter": {
                "evaluated_count": eligible.evaluated_count,
                "eligible_count": len(eligible.claim_ids),
                "blocked_reasons": dict(eligible.blocked_reasons),
            },
            "source_hit_counts": {
                trace.source: trace.hit_count for trace in execution.candidates.traces
            },
            "degradation_reasons": list(execution.candidates.degradation_reasons),
        }

    def query(
        self,
        text: str,
        top_k: int = 5,
        *,
        purpose: str = "recall",
        explain: bool = False,
        recorded_as_of: str | None = None,
        observed_as_of: str | None = None,
        valid_at: str | None = None,
        realm_id: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        recorded_runtime = self._recorded_runtime(recorded_as_of)
        if recorded_runtime is not self:
            return recorded_runtime.query(
                text,
                top_k,
                purpose=purpose,
                explain=explain,
                recorded_as_of=recorded_as_of,
                observed_as_of=observed_as_of,
                valid_at=valid_at,
                realm_id=realm_id,
                scope=scope,
            )
        limit = _required_positive_int(top_k, "top_k", 5)
        execution = self.execute(
            text,
            purpose=purpose,
            recorded_as_of=recorded_as_of,
            observed_as_of=observed_as_of,
            valid_at=valid_at,
            realm_id=realm_id,
            scope=scope,
        )
        ranked = execution.fusion.ranked_claims[:limit]
        rows = [self._row(execution, item) for item in ranked]
        return {
            "ok": True,
            "query": str(text or "").strip(),
            "count": len(rows),
            "results": rows,
            "retrieval_gateway": self.gateway_trace(execution),
            "ledger_v1": {
                "retrieval_profile": HYBRID_RETRIEVAL_PROFILE,
                "hybrid": self._trace(execution, full=bool(explain)),
            },
        }

    def _dense_vectors(self) -> Mapping[str, Sequence[float]] | None:
        embedding = self.config.embedding
        if not isinstance(embedding, Mapping) or embedding.get("enabled") is not True:
            return None
        snapshot = read_embedding_projection(self.paths.embedding_projection)
        if (
            snapshot is None
            or snapshot.built_from_ledger_head != self.state.ledger_head
            or snapshot.last_sequence != self.state.last_sequence
            or snapshot.logical_state_hash != self.state.logical_state_hash
        ):
            return None
        return snapshot.vectors

    def context(
        self,
        text: str,
        limit: int = 5,
        *,
        explain: bool = False,
        recorded_as_of: str | None = None,
        observed_as_of: str | None = None,
        valid_at: str | None = None,
        realm_id: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        recorded_runtime = self._recorded_runtime(recorded_as_of)
        if recorded_runtime is not self:
            return recorded_runtime.context(
                text,
                limit,
                explain=explain,
                recorded_as_of=recorded_as_of,
                observed_as_of=observed_as_of,
                valid_at=valid_at,
                realm_id=realm_id,
                scope=scope,
            )
        requested_limit = _required_positive_int(limit, "limit", 5)
        execution = self.execute(
            text,
            purpose="prepare_reply",
            recorded_as_of=recorded_as_of,
            observed_as_of=observed_as_of,
            valid_at=valid_at,
            realm_id=realm_id,
            scope=scope,
        )
        mmr_config = MMRConfig(
            max_claims=min(requested_limit, self.config.max_claims),
            max_per_subject=self.config.max_per_subject,
            max_per_predicate=self.config.max_per_predicate,
            max_per_subject_predicate=self.config.max_per_subject_predicate,
            max_fact_chars=self.config.max_fact_chars,
        )
        assembly = build_agent_context(
            execution.fusion.ranked_claims,
            self.state,
            execution.planning.plan,
            execution.eligibility.eligible,
            dense_vectors=self._dense_vectors(),
            config=mmr_config,
        )
        selected = set(assembly.selected_claim_ids)
        rows = [
            self._row(execution, item)
            for item in execution.fusion.ranked_claims
            if item.claim_id in selected
        ]
        skipped: Counter[str] = Counter()
        for trace in assembly.mmr_traces:
            if not trace.selected:
                skipped[trace.reason.split(":", 1)[0]] += 1
        for warning in assembly.warnings:
            skipped[warning.split(":", 1)[0]] += 1
        conflict_warnings = [
            trace.reason
            for trace in assembly.mmr_traces
            if "unresolved_conflict" in trace.reason
        ]
        gateway = self.gateway_trace(execution)
        hybrid_trace = self._trace(execution, full=bool(explain))
        if explain:
            hybrid_trace["assembly"] = assembly.to_dict()
        context_payload = {
            "context": assembly.context,
            "memory_context": assembly.context,
            "memories": rows,
            "citations": list(assembly.evidence_ids),
            "conflict_warnings": conflict_warnings,
            "skipped_reasons": dict(sorted(skipped.items())),
            "retrieval_gateway": gateway,
        }
        return {
            "ok": True,
            "query": str(text or "").strip(),
            "context": context_payload,
            "retrieval_gateway": gateway,
            "expression_mode": {},
            "system_prompt_extra": "",
            "context_with_expression": assembly.context,
            "recommended_actions": [],
            "ledger_v1": {
                "retrieval_profile": HYBRID_RETRIEVAL_PROFILE,
                "hybrid": hybrid_trace,
            },
        }


__all__ = [
    "HYBRID_RETRIEVAL_ENV_FLAG",
    "HYBRID_RETRIEVAL_PROFILE",
    "HybridExecution",
    "HybridRetrievalRuntime",
    "HybridRuntimeConfig",
    "HybridRuntimePaths",
]
