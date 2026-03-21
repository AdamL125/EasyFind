"""Embedding provider abstractions and Ollama implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import sqrt
from typing import List, Protocol, Sequence
from urllib import error, request

from .models import EmbeddingConfig

OLLAMA_BASE_URL = "http://127.0.0.1:11434"


class EmbeddingProvider(Protocol):
    """Provider interface for batch embedding generation."""

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return one embedding vector per text."""


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return cosine similarity for two dense vectors."""
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    numerator = sum(left[index] * right[index] for index in range(size))
    left_norm = sqrt(sum(value * value for value in left[:size]))
    right_norm = sqrt(sum(value * value for value in right[:size]))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


@dataclass(frozen=True)
class OllamaEmbeddingProvider:
    """Embedding provider backed by the local Ollama HTTP API."""

    model: str
    base_url: str = OLLAMA_BASE_URL

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not self.model:
            raise ValueError("Semantic search requires an embedding model name")
        if not texts:
            return []
        payload = {"model": self.model, "input": list(texts)}
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/api/embed",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req) as response:
                body = response.read().decode("utf-8")
        except error.URLError as exc:
            raise RuntimeError(f"Ollama embedding request failed: {exc}") from exc
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Ollama returned invalid JSON for embeddings") from exc
        embeddings = parsed.get("embeddings")
        if not isinstance(embeddings, list):
            single = parsed.get("embedding")
            if isinstance(single, list):
                embeddings = [single]
            else:
                raise RuntimeError("Ollama response did not include embeddings")
        return [[float(value) for value in embedding] for embedding in embeddings]


def build_embedding_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    """Build the configured embedding provider."""
    provider_name = config.provider.strip().lower()
    if provider_name == "ollama":
        return OllamaEmbeddingProvider(model=config.model)
    raise ValueError(f"Unsupported embedding provider: {config.provider}")
