"""Governed Hybrid Retrieval v1 package boundary.

The package is experimental and not wired into the default runtime path.
"""

from .candidate_sources import (
    CandidateSource,
    CandidateSourceError,
    run_candidate_source,
    validate_candidate_hits,
)
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
    "CandidateSource",
    "CandidateSourceError",
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
    "run_candidate_source",
    "validate_candidate_hits",
]
