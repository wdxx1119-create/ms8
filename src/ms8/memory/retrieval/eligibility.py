"""Fail-closed eligibility boundary for Hybrid Retrieval v1.

Every candidate source must receive an :class:`EligibleClaims` instance produced
by this module.  Ranking and retrieval implementations are not allowed to widen
that set.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ..application.replay import ClaimReplayView, ReplayState
from ..application.temporal_query import claim_is_valid_at, claim_was_observed_as_of, effective_valid_until
from .models import RetrievalPlan

_AUTHORITY_ALIASES = {
    "assistant_inferred": "agent_inferred",
}

_ALLOWED_STATUSES_BY_PURPOSE = {
    "recall": frozenset({"accepted", "verified", "disputed"}),
    "prepare_reply": frozenset({"accepted", "verified"}),
    "inject": frozenset({"accepted", "verified"}),
    "historical": frozenset({"accepted", "verified", "disputed", "superseded", "expired"}),
    "review": frozenset({"proposed", "accepted", "verified", "disputed"}),
    "audit": frozenset({"proposed", "accepted", "verified", "disputed", "superseded", "expired"}),
}


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str = "allowed"

    def __post_init__(self) -> None:
        reason = str(self.reason or "").strip()
        if not reason:
            raise ValueError("policy decision reason must not be empty")
        object.__setattr__(self, "reason", reason)


PolicyHook = Callable[[ClaimReplayView, RetrievalPlan, Mapping[str, bool]], PolicyDecision]


@dataclass(frozen=True, slots=True)
class EligibleClaims:
    """Immutable claim whitelist shared with candidate sources."""

    claim_ids: tuple[str, ...]
    blocked_reasons: Mapping[str, int] = field(default_factory=dict)
    evaluated_count: int = 0

    def __post_init__(self) -> None:
        normalized = tuple(str(value or "").strip() for value in self.claim_ids)
        if any(not value for value in normalized):
            raise ValueError("eligible claim identifiers must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("eligible claim identifiers must not contain duplicates")
        object.__setattr__(self, "claim_ids", tuple(sorted(normalized)))
        counts: dict[str, int] = {}
        for key, value in self.blocked_reasons.items():
            reason = str(key or "").strip()
            if not reason:
                raise ValueError("blocked reason must not be empty")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"blocked reason count must be non-negative: {reason}")
            counts[reason] = value
        object.__setattr__(self, "blocked_reasons", MappingProxyType(dict(sorted(counts.items()))))
        if isinstance(self.evaluated_count, bool) or not isinstance(self.evaluated_count, int) or self.evaluated_count < 0:
            raise ValueError("evaluated_count must be a non-negative integer")
        if len(normalized) > self.evaluated_count:
            raise ValueError("eligible claims cannot exceed evaluated claims")

    def allows(self, claim_id: str) -> bool:
        return str(claim_id or "").strip() in self.claim_ids

    def require(self, claim_id: str) -> None:
        normalized = str(claim_id or "").strip()
        if normalized not in self.claim_ids:
            raise PermissionError(f"claim is outside the retrieval eligibility set: {normalized or '<empty>'}")


@dataclass(frozen=True, slots=True)
class EligibilityEvaluation:
    eligible: EligibleClaims
    authority_aliases: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.eligible, EligibleClaims):
            raise TypeError("eligible must be EligibleClaims")
        object.__setattr__(self, "authority_aliases", MappingProxyType(dict(self.authority_aliases)))


def normalize_authority(authority: str) -> str:
    normalized = str(authority or "").strip()
    return _AUTHORITY_ALIASES.get(normalized, normalized)


def _governance(state: ReplayState, view: ClaimReplayView) -> dict[str, bool]:
    values = {
        "can_recall": True,
        "can_inject": False,
        "can_act_on": False,
        "quarantined": False,
    }
    for decision_id in view.decision_ids:
        decision = state.decisions.get(decision_id)
        if decision is None:
            continue
        configured = decision.policy.get("governance")
        if isinstance(configured, Mapping):
            for field_name in tuple(values):
                raw = configured.get(field_name)
                if isinstance(raw, bool):
                    values[field_name] = raw
        if decision.policy.get("quarantined") is True:
            values["quarantined"] = True
    return values


def _latest_action(state: ReplayState, view: ClaimReplayView) -> str | None:
    if not view.decision_ids:
        return None
    decision = state.decisions.get(view.decision_ids[-1])
    return decision.action if decision is not None else None


def _has_evidence(state: ReplayState, claim_id: str) -> bool:
    return any(evidence.claim_id == claim_id for evidence in state.evidence.values())


def _default_policy_hook(
    _view: ClaimReplayView,
    _plan: RetrievalPlan,
    _governance_values: Mapping[str, bool],
) -> PolicyDecision:
    return PolicyDecision(True, "allowed")


class EligibilityEvaluator:
    """Evaluate hard retrieval boundaries before any candidate source runs."""

    def __init__(self, policy_hook: PolicyHook | None = None) -> None:
        self.policy_hook = policy_hook or _default_policy_hook

    def evaluate(self, state: ReplayState, plan: RetrievalPlan) -> EligibilityEvaluation:
        if not isinstance(state, ReplayState):
            raise TypeError("state must be ReplayState")
        if not isinstance(plan, RetrievalPlan):
            raise TypeError("plan must be RetrievalPlan")

        purpose = plan.query.purpose
        allowed_statuses = _ALLOWED_STATUSES_BY_PURPOSE[purpose]
        blocked: Counter[str] = Counter()
        eligible: list[str] = []
        aliases_used: dict[str, str] = {}

        for claim_id in sorted(state.claims):
            view = state.claims[claim_id]
            reason = self._hard_boundary_reason(state, view, plan, allowed_statuses)
            if reason is not None:
                blocked[reason] += 1
                continue

            governance = _governance(state, view)
            policy = self.policy_hook(view, plan, MappingProxyType(governance))
            if not isinstance(policy, PolicyDecision):
                raise TypeError("policy_hook must return PolicyDecision")
            if not policy.allowed:
                blocked[policy.reason or "policy_denied"] += 1
                continue

            normalized_authority = normalize_authority(view.claim.authority)
            if normalized_authority != view.claim.authority:
                aliases_used[view.claim.authority] = normalized_authority
            eligible.append(claim_id)

        return EligibilityEvaluation(
            eligible=EligibleClaims(
                claim_ids=tuple(eligible),
                blocked_reasons=dict(blocked),
                evaluated_count=len(state.claims),
            ),
            authority_aliases=aliases_used,
        )

    @staticmethod
    def _hard_boundary_reason(
        state: ReplayState,
        view: ClaimReplayView,
        plan: RetrievalPlan,
        allowed_statuses: frozenset[str],
    ) -> str | None:
        claim = view.claim
        purpose = plan.query.purpose

        if plan.principal.capabilities and purpose not in plan.principal.capabilities and "all" not in plan.principal.capabilities:
            return "principal_capability_missing"
        if claim.realm_id not in plan.realm_ids:
            return "realm_not_planned"
        if claim.realm_id not in plan.principal.realm_ids:
            return "realm_not_authorized"
        if plan.query.realm_ids and claim.realm_id not in plan.query.realm_ids:
            return "query_realm_mismatch"
        if plan.query.scope is not None and claim.scope != plan.query.scope:
            return "query_scope_mismatch"
        if plan.principal.scopes and claim.scope not in plan.principal.scopes:
            return "scope_not_authorized"
        if claim.sensitivity not in plan.principal.allowed_sensitivities:
            return "sensitivity_not_authorized"
        if _latest_action(state, view) == "forget":
            return "forgotten"
        if view.current_status == "revoked":
            return "revoked"
        if view.current_status == "pending_review":
            return "pending_review"
        if view.current_status not in allowed_statuses:
            return "status_not_eligible"

        governance = _governance(state, view)
        if governance["quarantined"]:
            return "quarantined"
        if not governance["can_recall"]:
            return "recall_not_allowed"
        if purpose in {"prepare_reply", "inject"} and not governance["can_inject"]:
            return "inject_not_allowed"
        if purpose in {"prepare_reply", "inject"} and not view.decision_ids:
            return "missing_decision_trace"
        if purpose in {"prepare_reply", "inject"} and not _has_evidence(state, claim.claim_id):
            return "missing_evidence"

        time = plan.query.time
        if not claim_was_observed_as_of(state, view, time.observed_as_of):
            return "observed_after_cutoff"
        effective_end = effective_valid_until(state, view)
        if not claim_is_valid_at(claim, time.valid_at, effective_end=effective_end):
            return "outside_valid_time"
        return None


__all__ = [
    "EligibilityEvaluation",
    "EligibilityEvaluator",
    "EligibleClaims",
    "PolicyDecision",
    "PolicyHook",
    "normalize_authority",
]
