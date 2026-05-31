from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ms8.engine_core import local_llm as llm_mod


class _Client:
    def embeddings(self, model: str, prompt: str):  # noqa: ARG002
        return {"embedding": [0.2, 0.4]}

    def chat(self, model: str, messages: list[dict], options=None):  # noqa: ANN001, ARG002
        return {"message": {"content": "ollama-ok"}}


def test_provider_availability_with_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _Client())
    cfg = llm_mod.LLMConfig(failover_enabled=True, failover_max_errors=2, failover_cooldown_seconds=60)
    llm = llm_mod.LocalLLM(cfg)
    llm._provider_errors["openai"] = 2
    llm._provider_last_error_ts["openai"] = 10_000.0
    monkeypatch.setattr(llm_mod.time, "time", lambda: 10_020.0)
    assert llm._provider_available("openai") is False
    monkeypatch.setattr(llm_mod.time, "time", lambda: 10_100.0)
    assert llm._provider_available("openai") is True


def test_openai_chat_and_embedding_disabled_or_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _Client())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    llm = llm_mod.LocalLLM(llm_mod.LLMConfig(openai_enabled=False))
    with pytest.raises(RuntimeError):
        llm._openai_chat([{"role": "user", "content": "hi"}], via_openrouter=False, temperature=0.1, max_tokens=16)

    llm2 = llm_mod.LocalLLM(llm_mod.LLMConfig(openai_enabled=True, openai_api_key=""))
    with pytest.raises(RuntimeError):
        llm2._openai_embedding("x", via_openrouter=False)


def test_openai_chat_success_via_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _Client())
    cfg = llm_mod.LLMConfig(openai_enabled=True, openai_api_key="k")
    llm = llm_mod.LocalLLM(cfg)

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"choices": [{"message": {"content": "cloud-ok"}}]}

    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: _Resp())  # noqa: ANN001
    out = llm._openai_chat([{"role": "user", "content": "hello"}], via_openrouter=False, temperature=0.1, max_tokens=32)
    assert out == "cloud-ok"


def test_chat_when_all_providers_fail_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: None)
    cfg = llm_mod.LLMConfig(
        provider_order_chat=("openai", "openrouter", "ollama"),
        openai_enabled=False,
        openrouter_enabled=False,
        cache_enabled=False,
    )
    llm = llm_mod.LocalLLM(cfg)

    async def _run() -> str:
        return await llm.chat([{"role": "user", "content": "hi"}], use_cache=False, use_batch=False)

    out = asyncio.run(_run())
    assert out.startswith("[LLM Error] provider_chain_failed:")


def test_batchllm_merge_and_split_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "_create_ollama_client", lambda: _Client())
    batch = llm_mod.BatchLLM(llm_mod.LLMConfig(), llm_mod.SmartRouter(llm_mod.LLMConfig()))
    merged = batch._merge_prompts([[{"role": "user", "content": "A"}], [{"role": "user", "content": "B"}]])
    assert "任务1" in merged and "任务2" in merged
    # parse path
    ok = batch._split_results("任务 1: x\n任务 2: y", 2)
    assert ok == ["x", "y"]
    # fallback path
    fb = batch._split_results("n/a", 2)
    assert fb == ["n/a", "n/a"]
