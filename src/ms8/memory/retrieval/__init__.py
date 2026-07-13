"""Governed Hybrid Retrieval v1 package boundary.

The package is experimental and not wired into the default runtime path.
"""

from .analyzer import QueryAnalysis, analyze_query
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
from .query_planner import (
    ClassifierHook,
    QueryPlanner,
    QueryPlanningResult,
    TemporalResolution,
    resolve_temporal_expression,
)

__all__ = [
    "CandidateChannel",
    "CandidateHit",
    "CandidateLimits",
    "CandidateSource",
    "CandidateSourceError",
    "ClassifierHook",
    "EligibilityEvaluation",
    "EligibilityEvaluator",
    "EligibleClaims",
    "MemoryQuery",
    "PolicyDecision",
    "PolicyHook",
    "Principal",
    "PrincipalKind",
    "QueryAnalysis",
    "QueryIntent",
    "QueryPlanner",
    "QueryPlanningResult",
    "RankedClaim",
    "RetrievalPlan",
    "RetrievalPurpose",
    "RetrievalTrace",
    "TemporalResolution",
    "TimeCoordinates",
    "analyze_query",
    "normalize_authority",
    "resolve_temporal_expression",
    "run_candidate_source",
    "validate_candidate_hits",
]
