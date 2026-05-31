from __future__ import annotations

import asyncio
from typing import Any

from ms8.engine_core import local_llm as llm_mod


class _FakeClient:
    def __init__(self, fail_list: bool = False) -> None:
        self.fail_list = fail_list

    def embeddings(self, model: str, prompt: str) -> dict[str, list[float]]:  # noqa: ARG002
        return {"embedding": [0.1, 0.2, 0.3]}

    def chat(self, model: str, messages: list[dict], options: dict[str, Any] | None = None):  # noqa: ARG002
        return {"message": {"content": "ok"}}

    def list(self):
        if self.fail_list:
            raise RuntimeError("down")
        return {"models": [{"model": "gemma3:1b"}, {"name": "llama3.2:3b"}]}


def test_semantic_cache_embed_and_get_set(monkeypatch):
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _FakeClient())
    cache = llm_mod.SemanticCache("m", similarity_threshold=0.8, ttl=3600, embedding_func=lambda _t: [1.0, 2.0])
    cache.set("hello", "world")
    assert cache.get("hello") == "world"
    assert cache.stats()["size"] == 1


def test_semantic_cache_custom_embed_failure_fallback(monkeypatch):
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _FakeClient())

    def _boom(_text: str):
        raise RuntimeError("bad")

    cache = llm_mod.SemanticCache("m", embedding_func=_boom)
    vec = cache._embed("abc")
    assert len(vec) >= 1


def test_smart_router_complexity_and_selection():
    cfg = llm_mod.LLMConfig(complexity_threshold=0.3, reasoning_threshold=0.6)
    router = llm_mod.SmartRouter(cfg)
    complex_text = "因为这个必须这样，所以那个也必须这样，而且之前那个也要处理。"
    score = router.calculate_complexity(complex_text, "如果失败怎么办")
    assert 0.0 <= score <= 1.0
    assert router.select_model(complex_text, task_type="reasoning") == "reasoning"
    assert router.get_model_name("complex") == cfg.complex_model


def test_local_llm_provider_chain_and_secrets(monkeypatch):
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _FakeClient())
    cfg = llm_mod.LLMConfig(task_provider_order={"kg_extract": ("openai", "ollama")})
    llm = llm_mod.LocalLLM(cfg)
    assert llm._provider_chain("chat", task_type="kg_extract") == ["openai", "ollama"]
    assert llm._provider_chain("embedding", task_type="none")[0] in {"ollama", "openai", "openrouter"}


def test_local_llm_embed_fallback_when_all_providers_fail(monkeypatch):
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: None)
    cfg = llm_mod.LLMConfig(openai_enabled=False, openrouter_enabled=False)
    llm = llm_mod.LocalLLM(cfg)
    vec = llm._embed_text_raw("fallback")
    assert isinstance(vec, list)
    assert len(vec) == 1


def test_local_llm_chat_cache_and_fallback(monkeypatch):
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _FakeClient())
    cfg = llm_mod.LLMConfig(
        provider_order_chat=("openai", "ollama"),
        openai_enabled=False,
        cache_enabled=True,
    )
    llm = llm_mod.LocalLLM(cfg)

    async def _run():
        msgs = [{"role": "user", "content": "hello"}]
        first = await llm.chat(msgs, use_batch=False)
        second = await llm.chat(msgs, use_batch=False)
        return first, second

    first, second = asyncio.run(_run())
    assert first == "ok"
    assert second == "ok"
    assert llm.stats["provider_fallbacks"] >= 1
    assert llm.stats["cache_hits"] >= 1


def test_local_llm_model_info_success_and_error(monkeypatch):
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _FakeClient(fail_list=False))
    llm = llm_mod.LocalLLM(llm_mod.LLMConfig())
    info = llm.get_model_info()
    assert info["available"] is True
    assert "gemma3:1b" in info["models"]

    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _FakeClient(fail_list=True))
    llm2 = llm_mod.LocalLLM(llm_mod.LLMConfig(openai_enabled=False, openrouter_enabled=False))
    info2 = llm2.get_model_info()
    assert info2["available"] is False
    assert "error" in info2


def test_parse_validation_and_stats(monkeypatch):
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _FakeClient())
    llm = llm_mod.LocalLLM(llm_mod.LLMConfig())
    out = llm._parse_validation("一致性: 0.8\n清晰度: x\n结论: ok")
    assert out["一致性"] == 0.8
    assert out["清晰度"] == 0.5
    st = llm.get_stats()
    assert "config" in st and "provider_order_chat" in st["config"]
