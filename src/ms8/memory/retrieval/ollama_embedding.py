"""Optional Ollama embedding provider for Hybrid Retrieval v1.

The adapter uses the local HTTP API directly, so importing MS8 does not require the
``ollama`` Python package. Remote hosts are rejected unless the caller explicitly
opts in; the provider is never created or invoked by the default runtime path.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Sequence
from urllib.parse import urlparse

from .embedding import normalize_embedding_vector


class OllamaEmbeddingError(RuntimeError):
    """Raised when the optional local embedding provider is unavailable or invalid."""


def _validated_host(value: str, *, allow_remote: bool) -> str:
    host = str(value or "").strip().rstrip("/")
    if not host:
        raise ValueError("ollama host must not be empty")
    parsed = urlparse(host)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("ollama host must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("ollama host must not contain credentials, query, or fragment")
    loopback = parsed.hostname.casefold() in {"localhost", "127.0.0.1", "::1"}
    if not loopback and not allow_remote:
        raise ValueError("remote Ollama hosts require allow_remote=True")
    return host


class OllamaEmbeddingProvider:
    """Generate embeddings through Ollama's explicit ``/api/embed`` endpoint."""

    def __init__(
        self,
        *,
        model: str,
        dimensions: int,
        host: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 10.0,
        allow_remote: bool = False,
    ) -> None:
        model_name = str(model or "").strip()
        if not model_name:
            raise ValueError("ollama model must not be empty")
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
            raise ValueError("ollama dimensions must be a positive integer")
        timeout = float(timeout_seconds)
        if timeout <= 0.0:
            raise ValueError("ollama timeout_seconds must be positive")
        self._model = model_name
        self._dimensions = dimensions
        self._host = _validated_host(host, allow_remote=allow_remote)
        self._timeout_seconds = timeout

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        normalized_texts = tuple(str(text or "").strip() for text in texts)
        if not normalized_texts or any(not text for text in normalized_texts):
            raise ValueError("ollama embedding texts must not be empty")
        request = urllib.request.Request(
            self._host + "/api/embed",
            data=json.dumps(
                {"model": self._model, "input": list(normalized_texts)},
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OllamaEmbeddingError(f"ollama embedding request failed: {type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise OllamaEmbeddingError("ollama embedding response must be an object")
        raw_embeddings = payload.get("embeddings")
        if not isinstance(raw_embeddings, list) or len(raw_embeddings) != len(normalized_texts):
            raise OllamaEmbeddingError("ollama embedding response count mismatch")
        vectors: list[tuple[float, ...]] = []
        for index, raw_vector in enumerate(raw_embeddings):
            if not isinstance(raw_vector, list):
                raise OllamaEmbeddingError("ollama embedding vector must be a list")
            try:
                vector = normalize_embedding_vector(
                    raw_vector,
                    field_name=f"ollama embedding[{index}]",
                    expected_dimensions=self._dimensions,
                )
            except (TypeError, ValueError) as exc:
                raise OllamaEmbeddingError(str(exc)) from exc
            vectors.append(vector)
        return tuple(vectors)


__all__ = ["OllamaEmbeddingError", "OllamaEmbeddingProvider"]
