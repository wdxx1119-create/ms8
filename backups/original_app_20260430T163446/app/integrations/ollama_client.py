from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Tuple


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout_seconds: int = 8) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def classify(self, prompt: str) -> Tuple[bool, Dict[str, Any], str, Dict[str, Any]]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = resp.read().decode("utf-8")
            elapsed_ms = int((time.time() - started) * 1000)
            data = json.loads(body)
            text = data.get("response", "{}")
            parsed = json.loads(text)
            meta = {
                "elapsed_ms": elapsed_ms,
                "model": self.model,
                "prompt_preview": prompt[:220],
                "response_preview": text[:220],
            }
            return True, parsed, "", meta
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, Exception) as exc:
            elapsed_ms = int((time.time() - started) * 1000)
            meta = {
                "elapsed_ms": elapsed_ms,
                "model": self.model,
                "prompt_preview": prompt[:220],
            }
            return False, {}, str(exc), meta
