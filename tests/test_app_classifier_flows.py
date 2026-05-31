from __future__ import annotations

from ms8.app.classifier.context_builder import ContextBuilder
from ms8.app.classifier.hybrid_classifier import HybridClassifier
from ms8.app.classifier.llm_classifier import LLMClassifier
from ms8.app.classifier.threshold_manager import ThresholdManager
from ms8.app.config import ThresholdConfig


class _Rule:
    def __init__(self, result: tuple[str, float, list[str], list[str]]) -> None:
        self.result = result

    def classify(self, text: str) -> tuple[str, float, list[str], list[str]]:
        _ = text
        return self.result


class _LLM:
    def __init__(self, ok: bool, payload: dict | None = None, err: str = "") -> None:
        self.ok = ok
        self.payload = payload or {}
        self.err = err

    def classify(self, text: str, context: dict) -> tuple[bool, dict, str]:
        _ = (text, context)
        return self.ok, self.payload, self.err


class _Client:
    def __init__(self, resp: tuple[bool, dict, str, dict]) -> None:
        self.resp = resp

    def classify(self, prompt: str) -> tuple[bool, dict, str, dict]:
        assert "Allowed categories" in prompt
        return self.resp


def test_hybrid_classifier_rule_high_confidence_path() -> None:
    rule = _Rule(("decision", 0.91, ["d"], ["r1"]))
    llm = _LLM(False, err="should_not_be_called")
    tm = ThresholdManager(ThresholdConfig())
    h = HybridClassifier(rule_classifier=rule, llm_classifier=llm, threshold_manager=tm, use_llm=False)
    out = h.classify("x", {"has_code": False, "recent_categories": []})
    assert out["reason"] == "rule_high_confidence"
    assert out["llm_used"] is False


def test_hybrid_classifier_gray_area_without_llm() -> None:
    rule = _Rule(("", 0.0, [], []))
    llm = _LLM(False, err="off")
    tm = ThresholdManager(ThresholdConfig())
    h = HybridClassifier(rule_classifier=rule, llm_classifier=llm, threshold_manager=tm, use_llm=False)
    out = h.classify("x", {"has_code": False, "recent_categories": []})
    assert out["reason"].startswith("rule_fallback:no_rule_match")
    assert out["category"] == "technical_doc"


def test_hybrid_classifier_llm_error_fallback() -> None:
    rule = _Rule(("configuration", 0.6, ["cfg"], ["r"]))
    llm = _LLM(False, err="llm_down")
    cfg = ThresholdConfig()
    cfg.category_thresholds["configuration"] = 0.9
    tm = ThresholdManager(cfg)
    h = HybridClassifier(rule_classifier=rule, llm_classifier=llm, threshold_manager=tm, use_llm=True)
    out = h.classify("x", {"has_code": True, "recent_categories": []})
    assert out["llm_used"] is False
    assert out["llm_error"] == "llm_down"


def test_hybrid_classifier_llm_success_merges_tags() -> None:
    rule = _Rule(("configuration", 0.61, ["cfg"], ["r"]))
    llm = _LLM(True, payload={"category": "technical_doc", "confidence": 0.88, "tags": ["llm"], "reason": "mixed"})
    cfg = ThresholdConfig()
    cfg.category_thresholds["configuration"] = 0.9
    tm = ThresholdManager(cfg)
    h = HybridClassifier(rule_classifier=rule, llm_classifier=llm, threshold_manager=tm, use_llm=True)
    out = h.classify("x", {"has_code": False, "recent_categories": ["decision", "preference"]})
    assert out["llm_used"] is True
    assert out["category"] == "technical_doc"
    assert sorted(out["tags"]) == ["cfg", "llm"]


def test_llm_classifier_invalid_category_and_invalid_tags() -> None:
    client = _Client((True, {"category": "bad", "confidence": 0.9, "tags": "x"}, "", {"m": 1}))
    c = LLMClassifier(client=client, categories=["technical_doc", "decision"])
    ok, payload, err = c.classify("hello", {"k": "v"})
    assert ok is False
    assert err.startswith("invalid_category:")
    assert "llm_meta" in payload


def test_llm_classifier_success_clamps_confidence() -> None:
    client = _Client((True, {"category": "decision", "confidence": 1.9, "tags": ["a", 2], "reason": "ok"}, "", {"m": 1}))
    c = LLMClassifier(client=client, categories=["decision"])
    ok, payload, err = c.classify("hello", {"k": "v"})
    assert ok is True
    assert err == ""
    assert payload["confidence"] == 1.0
    assert payload["tags"] == ["a", "2"]


def test_context_builder_fallback_and_shared_context(monkeypatch) -> None:
    # fallback
    monkeypatch.setattr("ms8.app.classifier.context_builder._assemble_shared_context_material", None)
    monkeypatch.setattr("ms8.app.classifier.context_builder._project_classification_context", None)
    out = ContextBuilder().build("see https://a.com file.py ```x```", [{"category": "decision", "tags": ["t"]}])
    assert out["has_code"] is True
    assert "links" in out
    assert "files" in out

    # shared context path
    def _asm(text: str, latest_memories: list[dict]) -> dict:
        return {"text": text, "latest": latest_memories}

    def _proj(material: dict) -> dict:
        return {"projected": True, "size": len(material.get("latest", []))}

    monkeypatch.setattr("ms8.app.classifier.context_builder._assemble_shared_context_material", _asm)
    monkeypatch.setattr("ms8.app.classifier.context_builder._project_classification_context", _proj)
    out2 = ContextBuilder().build("x", [{"category": "a"}, {"category": "b"}])
    assert out2 == {"projected": True, "size": 2}
