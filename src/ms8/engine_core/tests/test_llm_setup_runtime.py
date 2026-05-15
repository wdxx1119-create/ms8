from __future__ import annotations

from pathlib import Path

import yaml

from ms8.runtime import configure_llm_mode_runtime, suggest_llm_mode


class _Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_suggest_llm_mode_cloud_when_no_ollama(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def _raise(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("ms8.runtime.requests.get", _raise)
    out = suggest_llm_mode()
    assert out["mode"] == "cloud"
    assert out["available"] is True
    assert out["chat_order"][0] == "openai"


def test_configure_llm_mode_runtime_writes_config(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    monkeypatch.setattr("ms8.runtime.requests.get", lambda *_args, **_kwargs: _Resp(503))
    out = configure_llm_mode_runtime(mode="auto")
    assert out["ok"] is True
    assert out["applied_mode"] == "cloud"

    cfg_file = tmp_path / ".ms8" / "config.yaml"
    assert cfg_file.exists()
    payload = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    llm_cfg = payload["memory"]["llm"]
    assert llm_cfg["enabled"] is True
    assert llm_cfg["provider_order_chat"][0] in {"openai", "openrouter"}
