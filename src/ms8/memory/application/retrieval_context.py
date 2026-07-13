"""Projection-backed retrieval and governed context assembly for ledger-v1.

Candidate generation uses the disposable search projection. Authoritative claim,
status, temporal, evidence, decision, and conflict data are re-hydrated from a
verified ledger replay before any result is returned. Historical recorded-time
queries use a deterministic ledger replay fallback because a current projection
cannot safely represent claims that disappeared from the latest searchable view.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..application.conflicts import describe_recorded_conflict
from ..application.projection_service import ProjectionCoordinator
from ..application.replay import ClaimReplayView, ReplayState
from ..application.temporal_query import (
    claim_is_valid_at,
    claim_was_observed_as_of,
    effective_valid_until,
    replay_recorded_as_of,
)
from ..infrastructure.projection_io import read_json_object
from ..ports.record_store import RecordStore

_VISIBLE_STATUSES = frozenset({"proposed", "pending_review", "accepted", "verified", "disputed"})
_WORD_PATTERN = re.compile(r"[a-z0-9_]+")
_CJK_RUN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_AUTHORITY_WEIGHT = {
    "user_explicit": 1.0,
    "system_observed": 0.85,
    "user_implicit": 0.7,
    "assistant_inferred": 0.55,
    "tool_generated": 0.45,
}
_STATUS_WEIGHT = {
    "verified": 1.0,
    "accepted": 0.9,
    "pending_review": 0.65,
    "disputed": 0.45,
    "proposed": 0.35,
}


class RetrievalError(RuntimeError):
    """Raised when retrieval input or a required projection is invalid."""


@dataclass(frozen=True, slots=True)
class RetrievalPolicyDecision:
    allowed: bool
    reason: str


PolicyEvaluator = Callable[[ClaimReplayView, Mapping[str, Any]], RetrievalPolicyDecision]
InjectionGuard = Callable[["RetrievalHit"], bool]


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    text: str
    limit: int = 10
    recorded_as_of: str | None = None
    observed_as_of: str | None = None
    valid_at: str | None = None
    realm_id: str | None = None
    scope: str | None = None

    def __post_init__(self) -> None:
        normalized = str(self.text or "").strip()
        if not normalized:
            raise ValueError("retrieval text must not be empty")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1:
            raise ValueError("retrieval limit must be a positive integer")
        object.__setattr__(self, "text", normalized)
        if self.realm_id is not None:
            object.__setattr__(self, "realm_id", str(self.realm_id).strip() or None)
        if self.scope is not None:
            object.__setattr__(self, "scope", str(self.scope).strip() or None)


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    claim_id: str
    text: str
    subject: str
    predicate: str
    realm_id: str
    scope: str
    authority: str
    sensitivity: str
    confidence: float
    current_status: str
    valid_from: str | None
    valid_until: str | None
    score: float
    matched_terms: tuple[str, ...]
    ranking_explanation: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    conflicts: tuple[Mapping[str, Any], ...]
    can_recall: bool
    can_inject: bool
    can_act_on: bool
    source_event_id: str
    candidate_source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "subject": self.subject,
            "predicate": self.predicate,
            "realm_id": self.realm_id,
            "scope": self.scope,
            "authority": self.authority,
            "sensitivity": self.sensitivity,
            "confidence": self.confidence,
            "current_status": self.current_status,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "score": self.score,
            "matched_terms": list(self.matched_terms),
            "ranking_explanation": list(self.ranking_explanation),
            "evidence_ids": list(self.evidence_ids),
            "decision_ids": list(self.decision_ids),
            "conflict_ids": list(self.conflict_ids),
            "conflicts": [dict(item) for item in self.conflicts],
            "can_recall": self.can_recall,
            "can_inject": self.can_inject,
            "can_act_on": self.can_act_on,
            "source_event_id": self.source_event_id,
            "candidate_source": self.candidate_source,
        }


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query: str
    ledger_head: str
    last_sequence: int
    candidate_source: str
    hits: tuple[RetrievalHit, ...]
    policy_trace: Mapping[str, Any]

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "ledger_head": self.ledger_head,
            "last_sequence": self.last_sequence,
            "candidate_source": self.candidate_source,
            "count": len(self.hits),
            "hits": [item.to_dict() for item in self.hits],
            "policy_trace": dict(self.policy_trace),
        }


@dataclass(frozen=True, slots=True)
class ContextItem:
    claim_id: str
    text: str
    citation: str
    estimated_tokens: int
    conflict_ids: tuple[str, ...]
    conflicts: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "citation": self.citation,
            "estimated_tokens": self.estimated_tokens,
            "conflict_ids": list(self.conflict_ids),
            "conflicts": [dict(item) for item in self.conflicts],
        }


@dataclass(frozen=True, slots=True)
class ContextAssemblyResult:
    context: str
    token_budget: int
    estimated_tokens: int
    selected: tuple[ContextItem, ...]
    citations: tuple[str, ...]
    conflict_warnings: tuple[str, ...]
    skipped_reasons: Mapping[str, int]
    assembled_from_ledger_head: str

    def to_dict(self) -> dict[str, object]:
        return {
            "context": self.context,
            "token_budget": self.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "selected": [item.to_dict() for item in self.selected],
            "citations": list(self.citations),
            "conflict_warnings": list(self.conflict_warnings),
            "skipped_reasons": dict(self.skipped_reasons),
            "assembled_from_ledger_head": self.assembled_from_ledger_head,
        }


def _terms(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    terms = set(_WORD_PATTERN.findall(normalized))
    for run in _CJK_RUN_PATTERN.findall(normalized):
        terms.update(run)
        if len(run) > 1:
            terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return tuple(sorted(term for term in terms if term))


def _claim_terms(view: ClaimReplayView) -> tuple[str, ...]:
    claim = view.claim
    return _terms(
        " ".join(
            (
                claim.text,
                claim.subject,
                claim.predicate,
                claim.realm_id,
                claim.scope,
                claim.authority,
                claim.sensitivity,
            )
        )
    )


def _governance(state: ReplayState, view: ClaimReplayView) -> dict[str, bool]:
    values = {"can_recall": True, "can_inject": False, "can_act_on": False}
    for decision_id in view.decision_ids:
        decision = state.decisions.get(decision_id)
        if decision is None:
            continue
        configured = decision.policy.get("governance")
        if not isinstance(configured, Mapping):
            continue
        for field_name in tuple(values):
            raw = configured.get(field_name)
            if isinstance(raw, bool):
                values[field_name] = raw
    return values


def _evidence_ids(state: ReplayState, claim_id: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            evidence_id
            for evidence_id, evidence in state.evidence.items()
            if evidence.claim_id == claim_id
        )
    )


def _conflict_ids(state: ReplayState, claim_id: str) -> tuple[str, ...]:
    identifiers: list[str] = []
    for conflict_id, payload in state.conflicts.items():
        raw_claim_ids = payload.get("claim_ids", ())
        if isinstance(raw_claim_ids, Sequence) and not isinstance(raw_claim_ids, str):
            if claim_id in {str(value) for value in raw_claim_ids}:
                identifiers.append(conflict_id)
    return tuple(sorted(identifiers))


def _conflict_details(
    state: ReplayState,
    conflict_ids: Sequence[str],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(describe_recorded_conflict(state, conflict_id) for conflict_id in conflict_ids)


def _default_policy(view: ClaimReplayView, context: Mapping[str, Any]) -> RetrievalPolicyDecision:
    if view.current_status not in _VISIBLE_STATUSES:
        return RetrievalPolicyDecision(False, "inactive_status")
    governance = context.get("governance")
    if not isinstance(governance, Mapping) or governance.get("can_recall") is not True:
        return RetrievalPolicyDecision(False, "recall_not_allowed")
    return RetrievalPolicyDecision(True, "allowed")


def _load_search_projection(path: Path) -> tuple[dict[str, Mapping[str, Any]], Mapping[str, Any]]:
    payload = read_json_object(path)
    if not isinstance(payload, Mapping):
        raise RetrievalError("search projection is missing or unreadable")
    raw_documents = payload.get("documents")
    raw_postings = payload.get("postings")
    if not isinstance(raw_documents, list) or not isinstance(raw_postings, Mapping):
        raise RetrievalError("search projection payload is invalid")
    documents: dict[str, Mapping[str, Any]] = {}
    for item in raw_documents:
        if not isinstance(item, Mapping):
            raise RetrievalError("search projection contains an invalid document")
        claim_id = str(item.get("claim_id") or "").strip()
        if not claim_id or claim_id in documents:
            raise RetrievalError("search projection claim identifiers are invalid")
        documents[claim_id] = item
    return documents, raw_postings


def _candidate_claim_ids(
    query_terms: tuple[str, ...],
    documents: Mapping[str, Mapping[str, Any]],
    postings: Mapping[str, Any],
) -> tuple[str, ...]:
    identifiers: set[str] = set()
    for term in query_terms:
        raw = postings.get(term, ())
        if isinstance(raw, Sequence) and not isinstance(raw, str):
            identifiers.update(str(value) for value in raw if str(value) in documents)
    return tuple(sorted(identifiers))


class RetrievalEngine:
    """Retrieve governed claims using a disposable candidate projection and ledger replay."""

    def __init__(
        self,
        *,
        record_store: RecordStore,
        projection_coordinator: ProjectionCoordinator,
        search_projection_path: Path,
        policy_evaluator: PolicyEvaluator | None = None,
    ) -> None:
        self.record_store = record_store
        self.projection_coordinator = projection_coordinator
        self.search_projection_path = Path(search_projection_path)
        self.policy_evaluator = policy_evaluator or _default_policy

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        readiness = self.projection_coordinator.require_ready_for_query()
        transactions = tuple(self.record_store.iterate())
        state = replay_recorded_as_of(transactions, request.recorded_as_of)
        query_terms = _terms(request.text)
        documents, postings = _load_search_projection(self.search_projection_path)

        if request.recorded_as_of is None:
            candidate_source = "search_projection"
            candidate_ids = _candidate_claim_ids(query_terms, documents, postings)
        else:
            candidate_source = "ledger_temporal_fallback"
            candidate_ids = tuple(
                sorted(
                    claim_id
                    for claim_id, view in state.claims.items()
                    if set(query_terms).intersection(_claim_terms(view))
                )
            )

        blocked_reasons: Counter[str] = Counter()
        evaluated = 0
        hits: list[RetrievalHit] = []
        for claim_id in candidate_ids:
            view = state.claims.get(claim_id)
            if view is None:
                blocked_reasons["claim_not_present_at_recorded_time"] += 1
                continue
            claim = view.claim
            if request.realm_id is not None and claim.realm_id != request.realm_id:
                blocked_reasons["realm_mismatch"] += 1
                continue
            if request.scope is not None and claim.scope != request.scope:
                blocked_reasons["scope_mismatch"] += 1
                continue
            if not claim_was_observed_as_of(state, view, request.observed_as_of):
                blocked_reasons["observed_after_cutoff"] += 1
                continue
            effective_end = effective_valid_until(state, view)
            if not claim_is_valid_at(claim, request.valid_at, effective_end=effective_end):
                blocked_reasons["outside_valid_time"] += 1
                continue

            governance = _governance(state, view)
            policy_context = {
                "governance": governance,
                "recorded_as_of": request.recorded_as_of,
                "observed_as_of": request.observed_as_of,
                "valid_at": request.valid_at,
                "realm_id": request.realm_id,
                "scope": request.scope,
            }
            evaluated += 1
            policy = self.policy_evaluator(view, policy_context)
            if not policy.allowed:
                blocked_reasons[policy.reason or "policy_denied"] += 1
                continue

            doc = documents.get(claim_id)
            candidate_terms = (
                tuple(str(value) for value in doc.get("terms", ()))
                if isinstance(doc, Mapping) and isinstance(doc.get("terms"), list)
                else _claim_terms(view)
            )
            matched = tuple(sorted(set(query_terms).intersection(candidate_terms)))
            lexical = len(matched) / max(1, len(query_terms))
            authority_score = _AUTHORITY_WEIGHT.get(claim.authority, 0.4)
            status_score = _STATUS_WEIGHT.get(view.current_status, 0.2)
            conflict_ids = _conflict_ids(state, claim_id)
            conflicts = _conflict_details(state, conflict_ids)
            score = (
                lexical * 0.55
                + claim.confidence * 0.2
                + authority_score * 0.15
                + status_score * 0.1
                - (0.05 if conflict_ids else 0.0)
            )
            explanation = (
                f"lexical_coverage={lexical:.3f}",
                f"confidence={claim.confidence:.3f}",
                f"authority_weight={authority_score:.3f}",
                f"status_weight={status_score:.3f}",
                "conflict_penalty=0.050" if conflict_ids else "conflict_penalty=0.000",
            )
            hits.append(
                RetrievalHit(
                    claim_id=claim_id,
                    text=claim.text,
                    subject=claim.subject,
                    predicate=claim.predicate,
                    realm_id=claim.realm_id,
                    scope=claim.scope,
                    authority=claim.authority,
                    sensitivity=claim.sensitivity,
                    confidence=claim.confidence,
                    current_status=view.current_status,
                    valid_from=claim.valid_time.start,
                    valid_until=effective_end,
                    score=round(score, 6),
                    matched_terms=matched,
                    ranking_explanation=explanation,
                    evidence_ids=_evidence_ids(state, claim_id),
                    decision_ids=view.decision_ids,
                    conflict_ids=conflict_ids,
                    conflicts=conflicts,
                    can_recall=governance["can_recall"],
                    can_inject=governance["can_inject"],
                    can_act_on=governance["can_act_on"],
                    source_event_id=claim.created_from_event_id,
                    candidate_source=candidate_source,
                )
            )

        hits.sort(key=lambda item: (-item.score, item.claim_id))
        selected = tuple(hits[: request.limit])
        policy_trace = {
            "projection_ready": readiness.ready_for_query,
            "projection_ledger_head": readiness.ledger_head,
            "candidate_count": len(candidate_ids),
            "evaluated_count": evaluated,
            "allowed_count": len(hits),
            "returned_count": len(selected),
            "blocked_count": sum(blocked_reasons.values()),
            "blocked_reasons": dict(sorted(blocked_reasons.items())),
            "time_coordinates": {
                "recorded_as_of": request.recorded_as_of,
                "observed_as_of": request.observed_as_of,
                "valid_at": request.valid_at,
            },
        }
        return RetrievalResult(
            query=request.text,
            ledger_head=state.ledger_head,
            last_sequence=state.last_sequence,
            candidate_source=candidate_source,
            hits=selected,
            policy_trace=policy_trace,
        )


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _estimate_tokens(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


class ContextAssembler:
    """Assemble injectable context with a second authorization and evidence check."""

    def __init__(
        self,
        *,
        token_budget: int = 1200,
        max_per_subject_predicate: int = 2,
        injection_guard: InjectionGuard | None = None,
    ) -> None:
        if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget < 1:
            raise ValueError("token_budget must be a positive integer")
        if (
            isinstance(max_per_subject_predicate, bool)
            or not isinstance(max_per_subject_predicate, int)
            or max_per_subject_predicate < 1
        ):
            raise ValueError("max_per_subject_predicate must be a positive integer")
        self.token_budget = token_budget
        self.max_per_subject_predicate = max_per_subject_predicate
        self.injection_guard = injection_guard

    def assemble(self, retrieval: RetrievalResult) -> ContextAssemblyResult:
        skipped: Counter[str] = Counter()
        seen_text: set[str] = set()
        seen_conflicts: set[str] = set()
        group_counts: Counter[tuple[str, str]] = Counter()
        selected: list[ContextItem] = []
        citations: list[str] = []
        conflict_warnings: list[str] = []
        lines: list[str] = []
        used_tokens = 0

        for hit in retrieval.hits:
            if not hit.can_inject:
                skipped["can_inject_false"] += 1
                continue
            if self.injection_guard is not None and not self.injection_guard(hit):
                skipped["injection_guard_denied"] += 1
                continue
            if not hit.evidence_ids:
                skipped["missing_evidence"] += 1
                continue
            if not hit.decision_ids:
                skipped["missing_decision_trace"] += 1
                continue
            normalized = _normalized_text(hit.text)
            if normalized in seen_text:
                skipped["duplicate_text"] += 1
                continue
            group = (hit.subject, hit.predicate)
            if group_counts[group] >= self.max_per_subject_predicate:
                skipped["diversity_limit"] += 1
                continue

            citation = (
                f"claim:{hit.claim_id};"
                f"evidence:{','.join(hit.evidence_ids)};"
                f"decisions:{','.join(hit.decision_ids)}"
            )
            line = f"- {hit.text} [{citation}]"
            estimated = _estimate_tokens(line)
            if used_tokens + estimated > self.token_budget:
                skipped["token_budget"] += 1
                continue

            seen_text.add(normalized)
            group_counts[group] += 1
            used_tokens += estimated
            lines.append(line)
            citations.append(citation)
            selected.append(
                ContextItem(
                    claim_id=hit.claim_id,
                    text=hit.text,
                    citation=citation,
                    estimated_tokens=estimated,
                    conflict_ids=hit.conflict_ids,
                    conflicts=hit.conflicts,
                )
            )
            for conflict in hit.conflicts:
                conflict_id = str(conflict.get("conflict_id") or "")
                if not conflict_id or conflict_id in seen_conflicts:
                    continue
                seen_conflicts.add(conflict_id)
                candidate_ids = [
                    str(item.get("claim_id") or "")
                    for item in conflict.get("candidates", [])
                    if isinstance(item, Mapping)
                ]
                explanation = conflict.get("recommendation_explanation", [])
                explanation_text = "; ".join(str(item) for item in explanation)
                conflict_warnings.append(
                    f"Conflict {conflict_id}: candidates={','.join(candidate_ids)}; "
                    f"recommended={conflict.get('recommended_claim_id')}; "
                    f"reason={conflict.get('reason')}; basis={explanation_text}"
                )

        return ContextAssemblyResult(
            context="\n".join(lines),
            token_budget=self.token_budget,
            estimated_tokens=used_tokens,
            selected=tuple(selected),
            citations=tuple(citations),
            conflict_warnings=tuple(conflict_warnings),
            skipped_reasons=dict(sorted(skipped.items())),
            assembled_from_ledger_head=retrieval.ledger_head,
        )


__all__ = [
    "ContextAssembler",
    "ContextAssemblyResult",
    "ContextItem",
    "RetrievalEngine",
    "RetrievalError",
    "RetrievalHit",
    "RetrievalPolicyDecision",
    "RetrievalRequest",
    "RetrievalResult",
]
