"""Governed Hybrid Retrieval v1 package boundary.

The package is experimental and not wired into the default runtime path.
"""

from .eligibility import (
    EligibilityEvaluation,
    EligibilityEvaluator,
    EligibleClaims,
    PolicyDecision,
    PolicyHook,
    normalize_authority,
)
from .models import (
    CandidateChannel,
    CandidateHit,
    CandidateLimits,
    MemoryQuery,
    Principal,
    PrincipalKind,
    QueryIntent,
    RankedClaim,
    RetrievalPlan,
    RetrievalPurpose,
    RetrievalTrace,
    TimeCoordinates,
)

__all__ = [
    "CandidateChannel",
    "CandidateHit",
    "CandidateLimits",
    "EligibilityEvaluation",
    "EligibilityEvaluator",
    "EligibleClaims",
    "MemoryQuery",
    "PolicyDecision",
    "PolicyHook",
    "Principal",
    "PrincipalKind",
    "QueryIntent",
    "RankedClaim",
    "RetrievalPlan",
    "RetrievalPurpose",
    "RetrievalTrace",
    "TimeCoordinates",
    "normalize_authority",
]
