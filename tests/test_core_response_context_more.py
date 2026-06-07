from __future__ import annotations

import collections
from pathlib import Path

import ms8.absorb.search as absorb_search
from ms8.engine_core.core import MemoryCore


class _WM:
    def __init__(self) -> None:
        self.logged: list[dict] = []

    def rank_search_results(self, query: str, results: list[dict], top_k: int | None = None) -> list[dict]:
        return []

    def restore_by_topic(self, topic_query: str, limit: int = 20) -> list[dict]:
        return []

    def build_injection_text(self, ranked_memories: list[dict], heading: str = "Relevant Memories", max_chars: int | None = None) -> str:
        return ""

    def log_usage(
        self,
        query: str,
        used_memories: list[dict],
        channel: str = "response_context",
        reason: str = "",
        candidates_count: int = 0,
        topic_hits_count: int = 0,
    ) -> None:
        self.logged.append(
            {
                "query": query,
                "used_count": len(used_memories),
                "reason": reason,
                "candidates_count": candidates_count,
                "topic_hits_count": topic_hits_count,
            }
        )


class _KF:
    def __init__(self) -> None:
        self.calls = 0

    def record_usage(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1


def _core(tmp_path: Path) -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c.config = {
        "memory_dir": tmp_path / "memory",
        "settings": {
            "memory": {
                "working_memory": {
                    "injection_top_k": 2,
                    "max_injection_chars": 180,
                    "dynamic_injection_budget": {"enabled": True, "topic_consistency_window": 3},
                    "force_injection_enabled": True,
                    "force_injection_min_items": 1,
                }
            }
        },
    }
    c.working_memory = _WM()
    c.knowledge_feedback = _KF()
    c.short_term_memory = collections.deque(maxlen=10)
    c._recent_query_tokens = collections.deque(maxlen=24)
    c._sync_openclaw_sessions = lambda force=False: None  # type: ignore[method-assign]
    c.retrieve_memories = lambda *a, **k: []  # type: ignore[method-assign]
    c._get_context_assist_signals = lambda message: {"is_followup": False}  # type: ignore[method-assign]
    c._recent_topic_consistency_score = lambda message, window=6: 0.0  # type: ignore[method-assign]
    c._compute_profile_assist_score = lambda p, query_type, cfg: 0.0  # type: ignore[method-assign]
    c._build_memory_ref = lambda source, title, content: f"{source}:{title}"  # type: ignore[method-assign]
    c._append_context_snapshot = lambda snapshot: None  # type: ignore[method-assign]
    c._query_tokens = lambda text: {"token"}  # type: ignore[method-assign]
    c._build_expression_mode_context = lambda message, payload: {"mode": "normal", "prompt_extra": "", "decision": {"reason": "ok"}}  # type: ignore[method-assign]
    c._get_synthetic_context_bundle = lambda limit=2: {"text": "", "candidate_ids": []}  # type: ignore[method-assign]
    c._memory_md_fallback_hits = lambda message, limit=2: [  # type: ignore[method-assign]
        {"id": "fb1", "source": "MEMORY.md", "title": "t", "content": "fallback memory", "score": 0.5}
    ]
    return c


def test_log_expression_decision_writes_jsonl(tmp_path: Path) -> None:
    c = _core(tmp_path)
    decision = type(
        "_D",
        (),
        {"to_dict": lambda self: {"mode": "light", "reason": "r", "confidence": 0.8}},  # type: ignore[misc]
    )()
    c._log_expression_decision(tmp_path / "memory", decision, 7)  # type: ignore[arg-type]
    out = (tmp_path / "memory" / "reports" / "expression_router_decisions.jsonl").read_text(encoding="utf-8")
    assert '"current_round": 7' in out
    assert '"mode": "light"' in out


def test_response_context_forced_fallback_and_expression_wrapper(tmp_path: Path) -> None:
    c = _core(tmp_path)
    payload = c.get_response_memory_context("where is memory")
    assert payload["should_inject"] is True
    assert payload["injection_reason"] == "forced_fallback_injection"
    assert "Relevant Memories" in payload["context"]
    assert payload["decision_trace"]["decision_reasons"]
    assert payload["context_with_expression"] == payload["context"]
    assert c.working_memory.logged[-1]["reason"] == "forced_fallback_injection"
    trace = payload["retrieval_gateway"]
    assert trace["purpose"] == "inject"
    assert trace["backend"] == "memory_core"
    assert "memory_md_fallback" in trace["reason_codes"]
    assert trace["health_signals"]["forced_injection_used"] is True


def test_response_context_expression_build_failure_falls_back(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c._build_expression_mode_context = lambda message, payload: (_ for _ in ()).throw(RuntimeError("x"))  # type: ignore[method-assign]
    payload = c.get_response_memory_context("q")
    assert payload["expression_mode"]["mode"] == "normal"
    assert payload["expression_mode"]["reason"] == "router_fallback_normal"


def test_response_context_with_expression_prompt_when_context_exists(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c._build_expression_mode_context = (  # type: ignore[method-assign]
        lambda _message, _payload: {"mode": "light", "prompt_extra": "Use concise structure", "decision": {"reason": "light"}}
    )
    payload = c.get_response_memory_context("where is memory")
    assert payload["system_prompt_extra"] == "Use concise structure"
    assert payload["context_with_expression"].startswith("[SYSTEM_PROMPT_EXTRA]\nUse concise structure")
    assert "[MEMORY_CONTEXT]\n" in payload["context_with_expression"]


def test_response_context_with_expression_prompt_when_context_empty(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c._memory_md_fallback_hits = lambda message, limit=2: []  # type: ignore[method-assign]
    c._build_expression_mode_context = (  # type: ignore[method-assign]
        lambda _message, _payload: {"mode": "light", "prompt_extra": "Only prompt", "decision": {"reason": "light"}}
    )
    payload = c.get_response_memory_context("q")
    assert payload["context"] == ""
    assert payload["context_with_expression"] == "[SYSTEM_PROMPT_EXTRA]\nOnly prompt"


def test_response_context_includes_absorb_local_documents(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    c = _core(tmp_path)
    c._memory_md_fallback_hits = lambda message, limit=2: []  # type: ignore[method-assign]
    c.config["settings"]["memory"]["working_memory"]["max_injection_chars"] = 600
    monkeypatch.setattr(
        absorb_search,
        "search_chunks",
        lambda query, limit=10: [
            {
                "chunk_id": "c1",
                "file_id": "f1",
                "canonical_path": "/docs/guide.md",
                "file_type": "md",
                "status": "LOCAL_INDEXED",
                "risk_level": "low",
                "score": 3.2,
                "search_backend": "sqlite",
                "text_preview": "Absorbed local project guide content",
            }
        ],
    )
    payload = c.get_response_memory_context("project guide")
    assert payload["should_inject"] is True
    assert "## Local Documents" in payload["context"]
    assert "Absorbed local project guide content" in payload["context"]
    assert payload["absorb_context"]["count"] == 1
    assert payload["decision_trace"]["absorb_total"] == 1
    assert "absorb_local_documents_included" in payload["decision_trace"]["decision_reasons"]
    assert payload["context_snapshot"]["absorb_injected_count"] == 1
    trace = payload["retrieval_gateway"]
    assert "absorb_context" in trace["reason_codes"]
    assert trace["health_signals"]["absorb_injected_count"] == 1


def test_response_context_skips_absorb_when_budget_too_small(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    c = _core(tmp_path)
    c._memory_md_fallback_hits = lambda message, limit=2: []  # type: ignore[method-assign]
    c.config["settings"]["memory"]["working_memory"]["max_injection_chars"] = 120
    c.config["settings"]["memory"]["working_memory"]["dynamic_injection_budget"]["enabled"] = False
    monkeypatch.setattr(
        absorb_search,
        "search_chunks",
        lambda query, limit=10: [
            {
                "chunk_id": "c1",
                "canonical_path": "/docs/guide.md",
                "file_type": "md",
                "risk_level": "low",
                "text_preview": "This should not fit",
            }
        ],
    )
    payload = c.get_response_memory_context("project guide")
    assert "## Local Documents" not in payload["context"]
    assert payload["absorb_context"]["count"] == 0
    assert payload["context_snapshot"]["absorb_injected_count"] == 0
