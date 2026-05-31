"""Policy engine interface for pluggable closed/open backends."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TypedDict


class PolicyEnvelope(TypedDict):
    ok: bool
    code: str
    reason: str
    trace_id: str
    data: dict[str, Any]


class PolicyEngine(Protocol):
    """Stable policy interface used by runtime, doctor, and core paths."""

    backend_name: str
    backend_version: str

    def evaluate_admission(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...
    async def aevaluate_admission(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...

    def rank_retrieval(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...
    async def arank_retrieval(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...

    def run_self_check_specs(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...
    async def arun_self_check_specs(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...

    def plan_self_repair(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...
    async def aplan_self_repair(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...

    def shadow_decide(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...
    async def ashadow_decide(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...

    # Optional semantic primitives for context understanding. Implementations may
    # omit these and let callers use local fallback heuristics.
    def classify_intent(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...

    def identify_topic(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        ...
