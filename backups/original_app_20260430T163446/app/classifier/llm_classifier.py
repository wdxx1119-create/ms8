from __future__ import annotations

from typing import Dict, List, Tuple

from app.integrations.ollama_client import OllamaClient


class LLMClassifier:
    def __init__(self, client: OllamaClient, categories: List[str]) -> None:
        self.client = client
        self.categories = categories

    def classify(self, text: str, context: Dict) -> Tuple[bool, Dict, str]:
        prompt = (
            "You are a strict classifier. Return JSON only with keys: "
            "category, confidence, tags, reason.\n"
            f"Allowed categories: {', '.join(self.categories)}\n"
            f"Context: {context}\n"
            f"Text: {text}\n"
        )
        ok, data, err, meta = self.client.classify(prompt)
        if not ok:
            return False, {"llm_meta": meta}, err
        category = str(data.get("category", "")).strip()
        confidence = float(data.get("confidence", 0.0) or 0.0)
        tags = data.get("tags", [])
        if category not in self.categories:
            return False, {"llm_meta": meta}, f"invalid_category:{category}"
        if not isinstance(tags, list):
            tags = []
        return True, {
            "category": category,
            "confidence": max(0.0, min(1.0, confidence)),
            "tags": [str(t) for t in tags][:10],
            "reason": str(data.get("reason", "llm_gray_area")),
            "llm_meta": meta,
        }, ""
