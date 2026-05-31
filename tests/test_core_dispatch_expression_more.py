from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import ms8.engine_core.core as core_mod
from ms8.engine_core.core import MemoryCore
from ms8.engine_core.response_mode_types import RouterDecision


class _KGOK:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def is_enabled(self) -> bool:
        return True

    def ingest_memory(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)


class _KGErr(_KGOK):
    def ingest_memory(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")


def _core_stub(tmp_path: Path) -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c.config = {
        "memory_dir": tmp_path / "memory",
        "settings": {"memory": {"expression_router": {"enabled": True, "profile": {"decay": 0.95}, "cooldown": {}}}},
    }
    return c


def test_dispatch_knowledge_graph_ingest_runs_thread_target(tmp_path: Path, monkeypatch) -> None:
    c = _core_stub(tmp_path)
    kg = _KGOK()
    c.knowledge_graph = kg

    class _InlineThread:
        def __init__(self, target=None, daemon=None):  # type: ignore[no-untyped-def]
            self._target = target

        def start(self) -> None:
            if self._target:
                self._target()

    monkeypatch.setattr(core_mod.threading, "Thread", _InlineThread)
    c._dispatch_knowledge_graph_ingest("src", "title", "content", use_llm=True)

    assert len(kg.calls) == 1
    assert kg.calls[0]["source"] == "src"
    assert kg.calls[0]["use_llm"] is True


def test_dispatch_knowledge_graph_ingest_skips_or_handles_error(tmp_path: Path, monkeypatch) -> None:
    c = _core_stub(tmp_path)
    c.knowledge_graph = _KGErr()

    class _InlineThread:
        def __init__(self, target=None, daemon=None):  # type: ignore[no-untyped-def]
            self._target = target

        def start(self) -> None:
            if self._target:
                self._target()

    monkeypatch.setattr(core_mod.threading, "Thread", _InlineThread)
    # should not raise when ingest raises
    c._dispatch_knowledge_graph_ingest("src", "title", "content", use_llm=False)
    # blank content path -> early return
    c._dispatch_knowledge_graph_ingest("src", "title", "   ", use_llm=False)


def test_build_expression_mode_context_fallback_normal(tmp_path: Path, monkeypatch) -> None:
    c = _core_stub(tmp_path)
    monkeypatch.setattr(core_mod, "resolve_expression_profile_dir", lambda p: tmp_path / "mem")
    monkeypatch.setattr(core_mod, "load_conversation_state", lambda *_: SimpleNamespace(current_round=0, last_cognitive_phrase=None))
    monkeypatch.setattr(core_mod, "load_expression_profile", lambda *_: {})
    monkeypatch.setattr(core_mod, "prepare_profile_for_round", lambda p, current_round, decay: (p, False))
    monkeypatch.setattr(core_mod, "route_response", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("router-down")))

    result = c._build_expression_mode_context("hello", {"context": "ctx"})
    assert result["mode"] == "normal"
    assert result["decision"]["reason"] == "router_fallback_normal"
    assert "全局安全边界" in result["prompt_extra"]


def test_build_expression_mode_context_success_updates_state_profile(tmp_path: Path, monkeypatch) -> None:
    c = _core_stub(tmp_path)
    saved: dict[str, object] = {}
    state = SimpleNamespace(current_round=4, last_cognitive_phrase="你会发现")
    profile = {"abstract_score": 0.8, "evidence_count": 5}
    decision = RouterDecision(mode="light", confidence=0.8, reason="threshold_light", profile_used=True)

    monkeypatch.setattr(core_mod, "resolve_expression_profile_dir", lambda p: tmp_path / "mem")
    monkeypatch.setattr(core_mod, "load_conversation_state", lambda *_: state)
    monkeypatch.setattr(core_mod, "load_expression_profile", lambda *_: profile)
    monkeypatch.setattr(core_mod, "prepare_profile_for_round", lambda p, current_round, decay: (p, True))
    monkeypatch.setattr(core_mod, "route_response", lambda **kwargs: decision)
    monkeypatch.setattr(core_mod, "choose_cognitive_phrase", lambda mode, last_phrase: "其实更像是")
    monkeypatch.setattr(core_mod, "build_profile_hint", lambda p: "PROFILE_HINT")
    monkeypatch.setattr(core_mod, "get_prompt_extra", lambda mode: "MODE_PROMPT")
    monkeypatch.setattr(
        core_mod,
        "update_conversation_state_with_policy",
        lambda s, d, reset_rounds_without_strong: SimpleNamespace(current_round=s.current_round + 1, last_cognitive_phrase=None),
    )
    monkeypatch.setattr(core_mod, "update_profile_from_decision", lambda p, d, current_round: {"updated": True})
    monkeypatch.setattr(core_mod, "save_conversation_state", lambda path, ns: saved.setdefault("state", ns))
    monkeypatch.setattr(core_mod, "save_expression_profile", lambda path, p: saved.setdefault("profile", p))
    monkeypatch.setattr(c, "_log_expression_decision", lambda memory_dir, d, r: saved.setdefault("logged_round", r))

    result = c._build_expression_mode_context("need help", {"context": "recent summary"})
    assert result["mode"] == "light"
    assert "PROFILE_HINT" in result["prompt_extra"]
    assert "本轮可优先使用认知转向句式" in result["prompt_extra"]
    assert "全局安全边界" in result["prompt_extra"]
    assert saved["logged_round"] == 5
