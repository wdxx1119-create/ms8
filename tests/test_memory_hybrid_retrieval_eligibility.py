from __future__ import annotations

from collections.abc import Mapping

import pytest

from ms8.memory.application.replay import ClaimReplayView, ReplayState
from ms8.memory.domain.models import Actor, Claim, Decision, Evidence, MemoryEvent, ValidTime
from ms8.memory.retrieval import (
    EligibilityEvaluator,
    MemoryQuery,
    PolicyDecision,
    Principal,
    RetrievalPlan,
    TimeCoordinates,
    normalize_authority,
)


def _state(
    *,
    current_status: str = "verified",
    realm_id: str = "realm:project",
    scope: str = "project",
    sensitivity: str = "private",
    authority: str = "user_explicit",
    can_recall: bool = True,
    can_inject: bool = True,
    decision_action: str = "review_accept",
    include_evidence: bool = True,
) -> ReplayState:
    event = MemoryEvent(
        event_id="evt_1",
        kind="user_input",
        content={"text": "The project supports Python 3.11-3.13."},
        source={"kind": "conversation", "locator": "turn:1"},
        observed_at="2026-07-12T09:00:00Z",
        observed_at_precision="exact",
        trust_class="user_explicit",
    )
    claim = Claim(
        claim_id="clm_1",
        kind="constraint",
        text="The project supports Python 3.11-3.13.",
        subject="project",
        predicate="python_support",
        value=["3.11", "3.12", "3.13"],
        scope=scope,
        realm_id=realm_id,
        authority=authority,
        sensitivity=sensitivity,
        confidence=1.0,
        status="proposed",
        valid_time=ValidTime(start="2026-01-01T00:00:00Z", basis="user_explicit"),
        created_from_event_id="evt_1",
    )
    decision = Decision(
        decision_id="dec_1",
        action=decision_action,
        target_claim_ids=("clm_1",),
        result_status=current_status,
        policy={
            "governance": {
                "can_recall": can_recall,
                "can_inject": can_inject,
                "can_act_on": False,
            }
        },
        actor=Actor(kind="user", id="user:test"),
        reason="test fixture",
        recorded_at="2026-07-12T09:01:00Z",
    )
    evidence = Evidence(
        evidence_id="evd_1",
        claim_id="clm_1",
        event_id="evt_1",
        relation="supports",
        fragment={"line": 1, "text": "The project supports Python 3.11-3.13."},
        quoted_text_hash="sha256:test",
        weight=1.0,
    )
    return ReplayState(
        ledger_head="sha256:head",
        last_sequence=1,
        memory_events={"evt_1": event},
        claims={"clm_1": ClaimReplayView(claim=claim, current_status=current_status, decision_ids=("dec_1",))},
        evidence={"evd_1": evidence} if include_evidence else {},
        decisions={"dec_1": decision},
        conflicts={},
        logical_state_hash="sha256:logical",
    )


def _plan(
    *,
    purpose: str = "prepare_reply",
    realm_ids: tuple[str, ...] = ("realm:project",),
    scopes: tuple[str, ...] = ("project",),
    sensitivities: tuple[str, ...] = ("private",),
) -> RetrievalPlan:
    principal = Principal(
        principal_id="user:test",
        kind="user",
        realm_ids=realm_ids,
        scopes=scopes,
        allowed_sensitivities=sensitivities,
        capabilities=(purpose,),
    )
    return RetrievalPlan(
        query=MemoryQuery(
            text="What Python versions are supported?",
            purpose=purpose,  # type: ignore[arg-type]
            time=TimeCoordinates(valid_at="2026-07-13T00:00:00Z"),
            realm_ids=realm_ids,
            scope=scopes[0] if scopes else None,
        ),
        principal=principal,
        intent="project_rule",
        realm_ids=realm_ids,
    )


def test_prepare_reply_returns_only_authorized_injectable_claims() -> None:
    evaluation = EligibilityEvaluator().evaluate(_state(), _plan())

    assert evaluation.eligible.claim_ids == ("clm_1",)
    assert evaluation.eligible.blocked_reasons == {}


def test_wrong_realm_is_removed_before_candidate_sources() -> None:
    evaluation = EligibilityEvaluator().evaluate(
        _state(realm_id="realm:personal"),
        _plan(realm_ids=("realm:project",)),
    )

    assert evaluation.eligible.claim_ids == ()
    assert evaluation.eligible.blocked_reasons == {"realm_not_planned": 1}


def test_prepare_reply_requires_can_inject_and_evidence() -> None:
    denied = EligibilityEvaluator().evaluate(_state(can_inject=False), _plan())
    missing_evidence = EligibilityEvaluator().evaluate(_state(include_evidence=False), _plan())

    assert denied.eligible.blocked_reasons == {"inject_not_allowed": 1}
    assert missing_evidence.eligible.blocked_reasons == {"missing_evidence": 1}


def test_sensitivity_and_scope_are_hard_boundaries() -> None:
    sensitivity = EligibilityEvaluator().evaluate(
        _state(sensitivity="secret"),
        _plan(sensitivities=("private",)),
    )
    scope = EligibilityEvaluator().evaluate(
        _state(scope="personal"),
        _plan(scopes=("project",)),
    )

    assert sensitivity.eligible.blocked_reasons == {"sensitivity_not_authorized": 1}
    assert scope.eligible.blocked_reasons == {"query_scope_mismatch": 1}


def test_forgotten_claim_is_never_a_retrieval_candidate() -> None:
    evaluation = EligibilityEvaluator().evaluate(
        _state(current_status="revoked", decision_action="forget"),
        _plan(purpose="audit"),
    )

    assert evaluation.eligible.claim_ids == ()
    assert evaluation.eligible.blocked_reasons == {"forgotten": 1}


def test_policy_hook_can_only_reduce_the_eligible_set() -> None:
    seen_governance: list[Mapping[str, bool]] = []

    def deny(_view: ClaimReplayView, _plan_value: RetrievalPlan, governance: Mapping[str, bool]) -> PolicyDecision:
        seen_governance.append(governance)
        return PolicyDecision(False, "policy_denied_fixture")

    evaluation = EligibilityEvaluator(policy_hook=deny).evaluate(_state(), _plan())

    assert evaluation.eligible.claim_ids == ()
    assert evaluation.eligible.blocked_reasons == {"policy_denied_fixture": 1}
    assert seen_governance[0]["can_inject"] is True
    with pytest.raises(TypeError):
        seen_governance[0]["can_inject"] = False  # type: ignore[index]


def test_authority_alias_is_normalized_without_rewriting_claim() -> None:
    state = _state(authority="assistant_inferred")
    evaluation = EligibilityEvaluator().evaluate(state, _plan())

    assert normalize_authority("assistant_inferred") == "agent_inferred"
    assert evaluation.authority_aliases == {"assistant_inferred": "agent_inferred"}
    assert state.claims["clm_1"].claim.authority == "assistant_inferred"


def test_eligible_set_rejects_out_of_boundary_claims() -> None:
    eligible = EligibilityEvaluator().evaluate(_state(), _plan()).eligible

    eligible.require("clm_1")
    with pytest.raises(PermissionError, match="outside the retrieval eligibility set"):
        eligible.require("clm_2")
