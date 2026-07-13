"""Conflict-aware MMR selection and compact governed Agent context assembly."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, cast

from ..application.replay import ClaimReplayView, ReplayState
from ..domain.models import Claim, Decision, Evidence
from .eligibility import EligibleClaims, normalize_authority
from .models import RankedClaim, RetrievalPlan

SimilarityMode = Literal["dense", "jaccard", "none"]

_CONTEXT_SCHEMA = "ms8.agent_context.v1"
_ASCII_TOKEN_PATTERN = re.compile(r"[a-z0-9_./:+-]+")
_CJK_RUN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_TOKEN_ESTIMATE_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9_./:+-]+|[^\s]"
)
_LOCATOR_TOKENS = (
    "offset",
    "line",
    "page",
    "path",
    "chunk",
    "span",
    "locator",
    "record_index",
    "section",
    "paragraph",
)


class ContextAssemblyError(RuntimeError):
    """Raised when a safe governed context cannot be assembled."""


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _finite_unit_interval(value: object, field_name: str) -> float:
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return number


def _unique_text(values: Sequence[object], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_required_text(item, f"{field_name}[]") for item in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


@dataclass(frozen=True, slots=True)
class MMRConfig:
    """Deterministic selection and diversity configuration."""

    relevance_lambda: float = 0.72
    max_claims: int = 12
    max_per_subject: int = 3
    max_per_predicate: int = 3
    max_per_subject_predicate: int = 2
    max_fact_chars: int = 320

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relevance_lambda",
            _finite_unit_interval(self.relevance_lambda, "mmr.relevance_lambda"),
        )
        for field_name in (
            "max_claims",
            "max_per_subject",
            "max_per_predicate",
            "max_per_subject_predicate",
            "max_fact_chars",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"mmr.{field_name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class MMRSelectionTrace:
    claim_id: str
    selected: bool
    relevance_score: float
    redundancy_score: float
    mmr_score: float
    similarity_mode: SimilarityMode
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _required_text(self.claim_id, "mmr_trace.claim_id"))
        object.__setattr__(
            self,
            "relevance_score",
            _finite_unit_interval(self.relevance_score, "mmr_trace.relevance_score"),
        )
        object.__setattr__(
            self,
            "redundancy_score",
            _finite_unit_interval(self.redundancy_score, "mmr_trace.redundancy_score"),
        )
        if not math.isfinite(self.mmr_score):
            raise ValueError("mmr_trace.mmr_score must be finite")
        if self.similarity_mode not in {"dense", "jaccard", "none"}:
            raise ValueError(f"unsupported similarity mode: {self.similarity_mode}")
        object.__setattr__(self, "reason", _required_text(self.reason, "mmr_trace.reason"))


@dataclass(frozen=True, slots=True)
class MMRSelectionResult:
    selected: tuple[RankedClaim, ...]
    traces: tuple[MMRSelectionTrace, ...]
    omitted_claim_ids: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if any(not isinstance(item, RankedClaim) for item in self.selected):
            raise TypeError("selection.selected must contain RankedClaim values")
        selected_ids = tuple(item.claim_id for item in self.selected)
        if len(set(selected_ids)) != len(selected_ids):
            raise ValueError("selection.selected must be claim-deduplicated")
        if any(not isinstance(item, MMRSelectionTrace) for item in self.traces):
            raise TypeError("selection.traces must contain MMRSelectionTrace values")
        object.__setattr__(
            self,
            "omitted_claim_ids",
            _unique_text(self.omitted_claim_ids, "selection.omitted_claim_ids"),
        )
        object.__setattr__(self, "warnings", _unique_text(self.warnings, "selection.warnings"))


@dataclass(frozen=True, slots=True)
class ContextAssemblyResult:
    schema: str
    context: str
    selected_claim_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    estimated_tokens: int
    budget_tokens: int
    reserved_metadata_tokens: int
    warnings: tuple[str, ...]
    mmr_traces: tuple[MMRSelectionTrace, ...]

    def __post_init__(self) -> None:
        if self.schema != _CONTEXT_SCHEMA:
            raise ValueError("context schema mismatch")
        object.__setattr__(self, "context", _required_text(self.context, "context.context"))
        object.__setattr__(
            self,
            "selected_claim_ids",
            _unique_text(self.selected_claim_ids, "context.selected_claim_ids"),
        )
        object.__setattr__(
            self,
            "evidence_ids",
            _unique_text(self.evidence_ids, "context.evidence_ids"),
        )
        object.__setattr__(
            self,
            "decision_ids",
            _unique_text(self.decision_ids, "context.decision_ids"),
        )
        for field_name in ("estimated_tokens", "budget_tokens", "reserved_metadata_tokens"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"context.{field_name} must be a non-negative integer")
        if self.budget_tokens < 1:
            raise ValueError("context.budget_tokens must be positive")
        if self.estimated_tokens > self.budget_tokens:
            raise ValueError("assembled context exceeds token budget")
        if self.reserved_metadata_tokens > self.estimated_tokens:
            raise ValueError("reserved metadata cannot exceed assembled context")
        object.__setattr__(self, "warnings", _unique_text(self.warnings, "context.warnings"))
        if any(not isinstance(item, MMRSelectionTrace) for item in self.mmr_traces):
            raise TypeError("context.mmr_traces must contain MMRSelectionTrace values")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "context": self.context,
            "selected_claim_ids": list(self.selected_claim_ids),
            "evidence_ids": list(self.evidence_ids),
            "decision_ids": list(self.decision_ids),
            "estimated_tokens": self.estimated_tokens,
            "budget_tokens": self.budget_tokens,
            "reserved_metadata_tokens": self.reserved_metadata_tokens,
            "warnings": list(self.warnings),
            "mmr_traces": [
                {
                    "claim_id": item.claim_id,
                    "selected": item.selected,
                    "relevance_score": item.relevance_score,
                    "redundancy_score": item.redundancy_score,
                    "mmr_score": item.mmr_score,
                    "similarity_mode": item.similarity_mode,
                    "reason": item.reason,
                }
                for item in self.mmr_traces
            ],
        }


def estimate_context_tokens(text: str) -> int:
    """Return a deterministic conservative token estimate without model dependencies."""

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return len(_TOKEN_ESTIMATE_PATTERN.findall(normalized))


def _similarity_tokens(claim: Claim) -> frozenset[str]:
    text = unicodedata.normalize(
        "NFKC",
        f"{claim.text} {claim.subject} {claim.predicate} {claim.value}",
    ).casefold()
    tokens: set[str] = set(_ASCII_TOKEN_PATTERN.findall(text))
    for run in _CJK_RUN_PATTERN.findall(text):
        tokens.update(run)
        tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return frozenset(token for token in tokens if token)


def _validated_vector(value: object) -> tuple[float, ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    vector: list[float] = []
    for item in value:
        try:
            number = float(cast(Any, item))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        vector.append(number)
    if not vector:
        return None
    norm = math.sqrt(sum(item * item for item in vector))
    if norm <= 0.0:
        return None
    return tuple(item / norm for item in vector)


def _pair_similarity(
    left_id: str,
    right_id: str,
    *,
    vectors: Mapping[str, tuple[float, ...]],
    tokens: Mapping[str, frozenset[str]],
) -> tuple[float, SimilarityMode]:
    left_vector = vectors.get(left_id)
    right_vector = vectors.get(right_id)
    if (
        left_vector is not None
        and right_vector is not None
        and len(left_vector) == len(right_vector)
    ):
        cosine = sum(left * right for left, right in zip(left_vector, right_vector, strict=True))
        return max(0.0, min(1.0, cosine)), "dense"
    left_tokens = tokens.get(left_id, frozenset())
    right_tokens = tokens.get(right_id, frozenset())
    union = left_tokens | right_tokens
    if not union:
        return 0.0, "jaccard"
    return len(left_tokens & right_tokens) / len(union), "jaccard"


def _unresolved_conflicts(
    state: ReplayState,
    visible_claim_ids: frozenset[str],
) -> tuple[Mapping[str, object], ...]:
    conflicts: list[Mapping[str, object]] = []
    for conflict_id in sorted(state.conflicts):
        conflict = state.conflicts[conflict_id]
        status = str(conflict.get("status") or "").casefold()
        if conflict.get("resolved") is True or status in {"resolved", "closed"}:
            continue
        raw_claim_ids = conflict.get("claim_ids", ())
        claim_ids = (
            tuple(str(item) for item in raw_claim_ids)
            if isinstance(raw_claim_ids, Sequence)
            and not isinstance(raw_claim_ids, (str, bytes, bytearray))
            else ()
        )
        visible = tuple(sorted(set(claim_ids).intersection(visible_claim_ids)))
        if not visible:
            continue
        conflicts.append(
            MappingProxyType(
                {
                    "conflict_id": conflict_id,
                    "visible_claim_ids": visible,
                    "hidden_members": len(visible) < len(set(claim_ids)),
                }
            )
        )
    return tuple(conflicts)


def _claims_by_conflict(
    conflicts: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, tuple[str, ...]], frozenset[str]]:
    by_claim: dict[str, list[str]] = defaultdict(list)
    protected: set[str] = set()
    for conflict in conflicts:
        conflict_id = str(conflict["conflict_id"])
        raw_visible = conflict["visible_claim_ids"]
        visible = tuple(str(item) for item in cast(Sequence[object], raw_visible))
        if len(visible) >= 2:
            protected.update(visible)
        for claim_id in visible:
            by_claim[claim_id].append(conflict_id)
    frozen = MappingProxyType(
        {claim_id: tuple(sorted(ids)) for claim_id, ids in sorted(by_claim.items())}
    )
    return frozen, frozenset(protected)


def _deduplicate_ranked(
    ranked_claims: Sequence[RankedClaim],
    eligible: EligibleClaims,
) -> tuple[tuple[RankedClaim, ...], tuple[str, ...]]:
    by_id: dict[str, RankedClaim] = {}
    warnings: list[str] = []
    for item in ranked_claims:
        if not isinstance(item, RankedClaim):
            raise TypeError("ranked_claims must contain RankedClaim values")
        eligible.require(item.claim_id)
        if item.claim_id in by_id:
            warnings.append(f"duplicate_ranked_claim:{item.claim_id}")
            continue
        by_id[item.claim_id] = item
    return tuple(by_id.values()), tuple(warnings)


def _valid_trace_ids(
    ranked: RankedClaim,
    view: ClaimReplayView,
    state: ReplayState,
    plan: RetrievalPlan,
) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    evidence_ids: list[str] = []
    for evidence_id in ranked.evidence_ids:
        evidence = state.evidence.get(evidence_id)
        if evidence is None:
            return (), (), f"missing_evidence:{ranked.claim_id}:{evidence_id}"
        if evidence.claim_id != ranked.claim_id:
            return (), (), f"evidence_claim_mismatch:{ranked.claim_id}:{evidence_id}"
        source_event = state.memory_events.get(evidence.event_id)
        if source_event is None:
            return (), (), f"missing_evidence_event:{ranked.claim_id}:{evidence_id}"
        evidence_ids.append(evidence_id)
    if not evidence_ids:
        return (), (), f"no_accessible_evidence:{ranked.claim_id}"

    decision_ids: list[str] = []
    for decision_id in view.decision_ids:
        decision = state.decisions.get(decision_id)
        if decision is None:
            return (), (), f"missing_decision:{ranked.claim_id}:{decision_id}"
        if (
            ranked.claim_id not in decision.target_claim_ids
            and decision.result_claim_id != ranked.claim_id
        ):
            return (), (), f"decision_claim_mismatch:{ranked.claim_id}:{decision_id}"
        decision_ids.append(decision_id)

    if plan.query.purpose in {"prepare_reply", "inject"}:
        if not decision_ids:
            return (), (), f"missing_decision_trace:{ranked.claim_id}"
        for evidence_id in evidence_ids:
            evidence = state.evidence[evidence_id]
            source_event = state.memory_events[evidence.event_id]
            if not source_event.source:
                return (), (), f"missing_evidence_source:{ranked.claim_id}:{evidence_id}"
            if not evidence.quoted_text_hash.startswith("sha256:"):
                return (), (), f"invalid_evidence_hash:{ranked.claim_id}:{evidence_id}"
            if not any(
                any(token in str(key).casefold() for token in _LOCATOR_TOKENS)
                and value not in (None, "", (), [], {})
                for key, value in evidence.fragment.items()
            ):
                return (), (), f"missing_evidence_locator:{ranked.claim_id}:{evidence_id}"
    return tuple(sorted(evidence_ids)), tuple(sorted(decision_ids)), None


def select_mmr(
    ranked_claims: Sequence[RankedClaim],
    state: ReplayState,
    plan: RetrievalPlan,
    eligible: EligibleClaims,
    *,
    dense_vectors: Mapping[str, Sequence[float]] | None = None,
    config: MMRConfig | None = None,
) -> MMRSelectionResult:
    """Select diverse claims while preserving unresolved conflict candidates."""

    if not isinstance(state, ReplayState):
        raise TypeError("state must be ReplayState")
    if not isinstance(plan, RetrievalPlan):
        raise TypeError("plan must be RetrievalPlan")
    if not isinstance(eligible, EligibleClaims):
        raise TypeError("eligible must be EligibleClaims")
    active = config or MMRConfig()
    if not isinstance(active, MMRConfig):
        raise TypeError("config must be MMRConfig")

    candidates, warnings = _deduplicate_ranked(ranked_claims, eligible)
    valid: list[RankedClaim] = []
    pre_omitted: list[str] = []
    trace_ids: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for item in candidates:
        view = state.claims.get(item.claim_id)
        if view is None:
            warnings = (*warnings, f"missing_claim:{item.claim_id}")
            pre_omitted.append(item.claim_id)
            continue
        evidence_ids, decision_ids, invalid_reason = _valid_trace_ids(item, view, state, plan)
        if invalid_reason is not None:
            warnings = (*warnings, invalid_reason)
            pre_omitted.append(item.claim_id)
            continue
        trace_ids[item.claim_id] = (evidence_ids, decision_ids)
        valid.append(item)

    visible_ids = frozenset(item.claim_id for item in valid)
    conflicts = _unresolved_conflicts(state, visible_ids)
    conflicts_by_claim, protected = _claims_by_conflict(conflicts)
    vectors: dict[str, tuple[float, ...]] = {}
    if dense_vectors is not None:
        for claim_id in visible_ids:
            vector = _validated_vector(dense_vectors.get(claim_id))
            if vector is not None:
                vectors[claim_id] = vector
    token_sets = {
        item.claim_id: _similarity_tokens(state.claims[item.claim_id].claim)
        for item in valid
    }
    position = {item.claim_id: index for index, item in enumerate(valid)}
    pool = list(valid)
    selected: list[RankedClaim] = []
    traces: list[MMRSelectionTrace] = []
    omitted: list[str] = list(pre_omitted)
    subject_counts: Counter[str] = Counter()
    predicate_counts: Counter[str] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()
    effective_limit = max(active.max_claims, len(protected))

    while pool and len(selected) < effective_limit:
        scored: list[tuple[tuple[object, ...], RankedClaim, float, float, SimilarityMode]] = []
        for item in pool:
            redundancy = 0.0
            mode: SimilarityMode = "none"
            for previous in selected:
                similarity, pair_mode = _pair_similarity(
                    item.claim_id,
                    previous.claim_id,
                    vectors=vectors,
                    tokens=token_sets,
                )
                if similarity > redundancy or (
                    math.isclose(similarity, redundancy) and pair_mode == "dense"
                ):
                    redundancy = similarity
                    mode = pair_mode
            relevance = max(0.0, min(1.0, item.score))
            mmr_score = (
                active.relevance_lambda * relevance
                - (1.0 - active.relevance_lambda) * redundancy
            )
            key = (
                0 if item.claim_id in protected else 1,
                item.hard_rule_tier,
                -round(mmr_score, 12),
                -round(relevance, 12),
                position[item.claim_id],
                item.claim_id,
            )
            scored.append((key, item, relevance, redundancy, mode))
        scored.sort(key=lambda value: value[0])
        _key, chosen, relevance, redundancy, mode = scored[0]
        pool.remove(chosen)
        view = state.claims[chosen.claim_id]
        subject = view.claim.subject.casefold()
        predicate = view.claim.predicate.casefold()
        pair = (subject, predicate)
        protected_claim = chosen.claim_id in protected
        limit_reason: str | None = None
        if not protected_claim:
            if subject_counts[subject] >= active.max_per_subject:
                limit_reason = "subject_diversity_limit"
            elif predicate_counts[predicate] >= active.max_per_predicate:
                limit_reason = "predicate_diversity_limit"
            elif pair_counts[pair] >= active.max_per_subject_predicate:
                limit_reason = "subject_predicate_diversity_limit"
        mmr_score = (
            active.relevance_lambda * relevance
            - (1.0 - active.relevance_lambda) * redundancy
        )
        if limit_reason is not None:
            omitted.append(chosen.claim_id)
            traces.append(
                MMRSelectionTrace(
                    claim_id=chosen.claim_id,
                    selected=False,
                    relevance_score=relevance,
                    redundancy_score=redundancy,
                    mmr_score=round(mmr_score, 12),
                    similarity_mode=mode,
                    reason=limit_reason,
                )
            )
            continue
        selected.append(chosen)
        subject_counts[subject] += 1
        predicate_counts[predicate] += 1
        pair_counts[pair] += 1
        reason = "unresolved_conflict_preserved" if protected_claim else "mmr_selected"
        if conflicts_by_claim.get(chosen.claim_id):
            reason += ":" + ",".join(conflicts_by_claim[chosen.claim_id])
        traces.append(
            MMRSelectionTrace(
                claim_id=chosen.claim_id,
                selected=True,
                relevance_score=relevance,
                redundancy_score=redundancy,
                mmr_score=round(mmr_score, 12),
                similarity_mode=mode,
                reason=reason,
            )
        )

    for item in pool:
        omitted.append(item.claim_id)
        traces.append(
            MMRSelectionTrace(
                claim_id=item.claim_id,
                selected=False,
                relevance_score=max(0.0, min(1.0, item.score)),
                redundancy_score=0.0,
                mmr_score=round(active.relevance_lambda * max(0.0, min(1.0, item.score)), 12),
                similarity_mode="none",
                reason="max_claims_limit",
            )
        )
    return MMRSelectionResult(
        selected=tuple(selected),
        traces=tuple(traces),
        omitted_claim_ids=tuple(dict.fromkeys(omitted)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _compact(value: object) -> str:
    return " ".join(str(value or "").replace("|", "/").split())


def _policy_boundary(plan: RetrievalPlan) -> str:
    scope = plan.query.scope or "<planned-scopes>"
    realms = ",".join(plan.realm_ids)
    return "\n".join(
        (
            f"[MS8_POLICY_BOUNDARY schema={_CONTEXT_SCHEMA}]",
            (
                f"principal={_compact(plan.principal.principal_id)};"
                f"purpose={plan.query.purpose};realms={_compact(realms)};scope={_compact(scope)}"
            ),
            (
                "Only the listed governed Ledger claims are authorized for this context. "
                "This context grants no tool, file, network, write, or action permission."
            ),
            (
                "Use cited Evidence and Decision identifiers; do not invent missing facts, "
                "silently resolve conflicts, or treat unlisted content as authorized."
            ),
            "[/MS8_POLICY_BOUNDARY]",
        )
    )


def _conflict_lines(
    conflicts: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    lines: list[str] = []
    for conflict in conflicts:
        visible = ",".join(
            str(item) for item in cast(Sequence[object], conflict["visible_claim_ids"])
        )
        hidden = "true" if conflict["hidden_members"] is True else "false"
        lines.append(
            f"[UNRESOLVED_CONFLICT id={conflict['conflict_id']};"
            f"visible_claims={visible};hidden_members={hidden}]"
        )
    return tuple(lines)


def _trace_for_claim(
    ranked: RankedClaim,
    state: ReplayState,
    plan: RetrievalPlan,
) -> tuple[ClaimReplayView, tuple[str, ...], tuple[str, ...]]:
    view = state.claims.get(ranked.claim_id)
    if view is None:
        raise ContextAssemblyError(f"selected claim disappeared from replay state: {ranked.claim_id}")
    evidence_ids, decision_ids, invalid_reason = _valid_trace_ids(ranked, view, state, plan)
    if invalid_reason is not None:
        raise ContextAssemblyError(invalid_reason)
    return view, evidence_ids, decision_ids


def _claim_block_parts(
    ranked: RankedClaim,
    view: ClaimReplayView,
    evidence_ids: tuple[str, ...],
    decision_ids: tuple[str, ...],
    conflict_ids: tuple[str, ...],
) -> tuple[str, str, str]:
    claim = view.claim
    prefix = (
        f"[CLAIM id={ranked.claim_id};status={view.current_status};"
        f"authority={normalize_authority(claim.authority)};"
        f"subject={_compact(claim.subject)};predicate={_compact(claim.predicate)}] fact="
    )
    fact = _compact(claim.text)
    suffix = (
        f" | evidence={','.join(evidence_ids)}"
        f" | decisions={','.join(decision_ids) if decision_ids else 'none'}"
        f" | conflicts={','.join(conflict_ids) if conflict_ids else 'none'}"
    )
    return prefix, fact, suffix


def _fit_claim_block(
    prefix: str,
    fact: str,
    suffix: str,
    *,
    remaining_tokens: int,
    max_fact_chars: int,
) -> str | None:
    limited = fact[:max_fact_chars]
    full = prefix + limited + suffix
    if estimate_context_tokens(full) <= remaining_tokens:
        return full
    minimal = prefix + suffix
    if estimate_context_tokens(minimal) > remaining_tokens:
        return None
    low = 0
    high = len(limited)
    best = ""
    while low <= high:
        middle = (low + high) // 2
        candidate_fact = limited[:middle].rstrip()
        if middle < len(limited):
            candidate_fact += "…"
        candidate = prefix + candidate_fact + suffix
        if estimate_context_tokens(candidate) <= remaining_tokens:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best or minimal


def assemble_context(
    selection: MMRSelectionResult,
    state: ReplayState,
    plan: RetrievalPlan,
    eligible: EligibleClaims,
    *,
    config: MMRConfig | None = None,
) -> ContextAssemblyResult:
    """Render compact claim facts with citations inside the explicit token budget."""

    if not isinstance(selection, MMRSelectionResult):
        raise TypeError("selection must be MMRSelectionResult")
    if not isinstance(state, ReplayState):
        raise TypeError("state must be ReplayState")
    if not isinstance(plan, RetrievalPlan):
        raise TypeError("plan must be RetrievalPlan")
    if not isinstance(eligible, EligibleClaims):
        raise TypeError("eligible must be EligibleClaims")
    active = config or MMRConfig()
    if not isinstance(active, MMRConfig):
        raise TypeError("config must be MMRConfig")

    selected_ids = frozenset(item.claim_id for item in selection.selected)
    conflicts = _unresolved_conflicts(state, selected_ids)
    conflicts_by_claim, _protected = _claims_by_conflict(conflicts)
    metadata_parts = (_policy_boundary(plan), *_conflict_lines(conflicts))
    metadata_text = "\n".join(metadata_parts)
    metadata_tokens = estimate_context_tokens(metadata_text)
    if metadata_tokens > plan.context_budget_tokens:
        raise ContextAssemblyError(
            "context budget is too small for required policy and conflict metadata"
        )

    parts = list(metadata_parts)
    included_claim_ids: list[str] = []
    included_evidence_ids: list[str] = []
    included_decision_ids: list[str] = []
    warnings = list(selection.warnings)
    used_tokens = metadata_tokens

    for ranked in selection.selected:
        eligible.require(ranked.claim_id)
        view, evidence_ids, decision_ids = _trace_for_claim(ranked, state, plan)
        prefix, fact, suffix = _claim_block_parts(
            ranked,
            view,
            evidence_ids,
            decision_ids,
            conflicts_by_claim.get(ranked.claim_id, ()),
        )
        separator_tokens = 1
        remaining = plan.context_budget_tokens - used_tokens - separator_tokens
        block = _fit_claim_block(
            prefix,
            fact,
            suffix,
            remaining_tokens=max(0, remaining),
            max_fact_chars=active.max_fact_chars,
        )
        if block is None:
            warnings.append(f"budget_omitted:{ranked.claim_id}")
            continue
        parts.append(block)
        used_tokens = estimate_context_tokens("\n".join(parts))
        included_claim_ids.append(ranked.claim_id)
        included_evidence_ids.extend(evidence_ids)
        included_decision_ids.extend(decision_ids)

    context = "\n".join(parts)
    estimated = estimate_context_tokens(context)
    if estimated > plan.context_budget_tokens:
        raise ContextAssemblyError("assembled context exceeded the declared token budget")
    return ContextAssemblyResult(
        schema=_CONTEXT_SCHEMA,
        context=context,
        selected_claim_ids=tuple(included_claim_ids),
        evidence_ids=tuple(dict.fromkeys(included_evidence_ids)),
        decision_ids=tuple(dict.fromkeys(included_decision_ids)),
        estimated_tokens=estimated,
        budget_tokens=plan.context_budget_tokens,
        reserved_metadata_tokens=metadata_tokens,
        warnings=tuple(dict.fromkeys(warnings)),
        mmr_traces=selection.traces,
    )


def build_agent_context(
    ranked_claims: Sequence[RankedClaim],
    state: ReplayState,
    plan: RetrievalPlan,
    eligible: EligibleClaims,
    *,
    dense_vectors: Mapping[str, Sequence[float]] | None = None,
    config: MMRConfig | None = None,
) -> ContextAssemblyResult:
    """Run governed MMR and compact assembly as one Phase 7 operation."""

    active = config or MMRConfig()
    selection = select_mmr(
        ranked_claims,
        state,
        plan,
        eligible,
        dense_vectors=dense_vectors,
        config=active,
    )
    return assemble_context(selection, state, plan, eligible, config=active)


__all__ = [
    "ContextAssemblyError",
    "ContextAssemblyResult",
    "MMRConfig",
    "MMRSelectionResult",
    "MMRSelectionTrace",
    "SimilarityMode",
    "assemble_context",
    "build_agent_context",
    "estimate_context_tokens",
    "select_mmr",
]
