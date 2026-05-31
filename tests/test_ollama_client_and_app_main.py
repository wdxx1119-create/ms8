from __future__ import annotations

import io
import json
from pathlib import Path

from ms8.app.integrations.ollama_client import OllamaClient
from ms8.app.main import build_pipeline


class _Resp:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_ollama_client_classify_success(monkeypatch) -> None:
    def _fake_urlopen(req, timeout=0):
        payload = {"response": json.dumps({"label": "decision", "score": 0.91})}
        return _Resp(json.dumps(payload))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    c = OllamaClient(base_url="http://127.0.0.1:11434", model="qwen3:8b", timeout_seconds=3)
    ok, parsed, err, meta = c.classify("决定用方案 B")
    assert ok is True
    assert parsed["label"] == "decision"
    assert err == ""
    assert meta["model"] == "qwen3:8b"
    assert "elapsed_ms" in meta
    assert "prompt_preview" in meta
    assert "response_preview" in meta


def test_ollama_client_classify_error_on_bad_json(monkeypatch) -> None:
    def _fake_urlopen(req, timeout=0):
        # response field is not valid json -> json.JSONDecodeError in second parse
        return _Resp(json.dumps({"response": "{bad-json"}))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    c = OllamaClient(base_url="http://127.0.0.1:11434", model="qwen3:8b")
    ok, parsed, err, meta = c.classify("test")
    assert ok is False
    assert parsed == {}
    assert isinstance(err, str) and err
    assert meta["model"] == "qwen3:8b"
    assert "elapsed_ms" in meta
    assert "prompt_preview" in meta
    assert "response_preview" not in meta


def test_build_pipeline_uses_auto_memory_defaults(tmp_path: Path) -> None:
    p = build_pipeline(tmp_path, {})
    # MemoryPipeline + AutoMemoryConfig were built with defaults
    assert p.workspace_dir == tmp_path
    assert p.config.enabled is True
    assert p.config.thresholds.global_min_confidence == 0.55


def test_build_pipeline_uses_overrides(tmp_path: Path) -> None:
    settings = {
        "auto_memory": {
            "enabled": True,
            "max_per_interaction": 7,
            "use_llm": False,
            "thresholds": {"global_min_confidence": 0.7},
        }
    }
    p = build_pipeline(tmp_path, settings)
    assert p.config.enabled is True
    assert p.config.thresholds.global_min_confidence == 0.7
    assert p.config.max_per_interaction == 7
    assert p.config.use_llm is False
