from __future__ import annotations

import asyncio

from ms8.engine_core.core import MemoryCore
from ms8.engine_core.local_llm import LLMConfig, LocalLLM


def test_chat_fallback_from_ollama_to_openai(monkeypatch) -> None:
    cfg = LLMConfig(
        provider_order_chat=("ollama", "openai"),
        provider_order_embedding=("ollama", "openai"),
        cache_enabled=False,
        batch_enabled=False,
        openai_enabled=True,
        openai_api_key="dummy",
    )
    llm = LocalLLM(cfg)

    # force ollama unavailable
    llm.client = None

    def _fake_openai_chat(messages, *, via_openrouter, temperature, max_tokens):
        assert via_openrouter is False
        assert isinstance(messages, list)
        return "fallback-ok"

    monkeypatch.setattr(llm, "_openai_chat", _fake_openai_chat)
    out = asyncio.run(llm.chat([{"role": "user", "content": "hello"}], use_batch=False))
    assert out == "fallback-ok"
    assert llm.stats["provider_fallbacks"] >= 1


def test_embedding_fallback_from_ollama_to_openai(monkeypatch) -> None:
    cfg = LLMConfig(
        provider_order_chat=("ollama", "openai"),
        provider_order_embedding=("ollama", "openai"),
        cache_enabled=False,
        openai_enabled=True,
        openai_api_key="dummy",
    )
    llm = LocalLLM(cfg)
    llm.client = None

    def _fake_openai_embedding(text: str, *, via_openrouter: bool):
        assert via_openrouter is False
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(llm, "_openai_embedding", _fake_openai_embedding)
    vec = llm._embed_text_raw("abc")
    assert vec == [0.1, 0.2, 0.3]


def test_task_specific_provider_routing(monkeypatch) -> None:
    cfg = LLMConfig(
        provider_order_chat=("openai",),
        task_provider_order={"kg_extract": ("ollama", "openrouter")},
        cache_enabled=False,
        batch_enabled=False,
        openrouter_enabled=True,
        openrouter_api_key="dummy",
    )
    llm = LocalLLM(cfg)
    llm.client = None  # ollama unavailable -> should fallback to openrouter for kg_extract

    def _fake_openai_chat(messages, *, via_openrouter, temperature, max_tokens):
        assert via_openrouter is True
        return "kg-ok"

    monkeypatch.setattr(llm, "_openai_chat", _fake_openai_chat)
    out = asyncio.run(llm.chat([{"role": "user", "content": "extract"}], task_type="kg_extract", use_batch=False))
    assert out == "kg-ok"


def test_core_builds_llm_config_from_settings() -> None:
    core = MemoryCore.__new__(MemoryCore)
    core.config = {
        "settings": {
            "memory": {
                "llm": {
                    "timeout_seconds": 33,
                    "provider_order_chat": ["openai", "ollama"],
                    "provider_order_embedding": ["openrouter", "ollama"],
                    "task_provider_order": {"kg_extract": ["ollama", "openrouter"]},
                    "openai": {"enabled": True, "chat_model": "gpt-4.1-mini"},
                    "openrouter": {"enabled": True, "chat_model": "openai/gpt-4.1-mini"},
                    "models": {"primary_model": "gemma3:1b"},
                }
            }
        }
    }
    cfg = core._build_llm_config_from_settings()
    assert cfg.llm_timeout_seconds == 33
    assert cfg.provider_order_chat == ("openai", "ollama")
    assert cfg.provider_order_embedding == ("openrouter", "ollama")
    assert cfg.task_provider_order["kg_extract"] == ("ollama", "openrouter")
