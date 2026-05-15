from .dedupe import dedupe_check
from .memory_admission_engine import AdmissionDecision, evaluate_candidate
from .quality_gate import quality_gate


class MemoryAdmissionEngine:
    """Compatibility wrapper expected by engine_core.core."""

    def __init__(self, records_path=None):
        self.records_path = records_path

    def admit(self, text: str, category: str = "general"):
        decision = evaluate_candidate(str(text or ""), metadata={"category": category})
        return {
            "allowed": decision.route not in {"rejected", "short_term_only"},
            "reason": ",".join(decision.reasons) if decision.reasons else decision.route,
            "route": decision.route,
            "normalized_text": decision.normalized_text,
            "should_persist_main": decision.should_persist_main,
            "should_index": decision.should_index,
            "should_write_memory_md": decision.should_write_memory_md,
            "redacted": decision.redacted,
            "replace_old": decision.replace_old,
            "risk_scores": decision.risk_scores,
            "privacy_flags": decision.privacy_flags,
            "conflict_flags": decision.conflict_flags,
            "raw": decision.raw,
        }


__all__ = [
    "dedupe_check",
    "quality_gate",
    "AdmissionDecision",
    "evaluate_candidate",
    "MemoryAdmissionEngine",
]
