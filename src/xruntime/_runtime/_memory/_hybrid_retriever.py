# -*- coding: utf-8 -*-
"""Hybrid memory retriever — keyword + vector search.

Combines exact keyword matching with semantic vector similarity
for improved recall and precision. Falls back to keyword-only
when no embedding model is configured.
"""
from __future__ import annotations

import math
from typing import Protocol

from ._models import MemoryItem
from ._store import MemoryStore


class EmbeddingProvider(Protocol):
    """Protocol for embedding model providers."""

    def embed(self, text: str) -> list[float]:
        """Embed text into a vector.

        Args:
            text: Input text.

        Returns:
            Embedding vector.
        """
        ...


class KeywordEmbeddingProvider:
    """Fallback embedding using keyword hashing.

    No external dependencies. Produces a sparse bag-of-words
    vector that captures exact word presence. Useful as a
    zero-dependency fallback that is slightly better than pure
    keyword overlap (supports partial matching via character
    n-grams).
    """

    def __init__(self, dim: int = 256) -> None:
        """Initialize."""
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        """Embed text via character n-gram hashing.

        Produces a dense vector where each dimension is a
        hashed character trigram count. This captures partial
        word matching (e.g., "deploy" ~ "deployment").

        Args:
            text: Input text.

        Returns:
            Dense embedding vector of size ``dim``.
        """
        vec = [0.0] * self._dim
        text_lower = text.lower()

        # Character trigrams
        for i in range(len(text_lower) - 2):
            trigram = text_lower[i : i + 3]
            idx = hash(trigram) % self._dim
            vec[idx] += 1.0

        # Word-level bigrams (for better phrase matching)
        words = text_lower.split()
        for i in range(len(words) - 1):
            bigram = f"{words[i]}_{words[i + 1]}"
            idx = hash(bigram) % self._dim
            vec[idx] += 2.0

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Similarity score in [0, 1].
    """
    if len(a) != len(b):
        min_len = min(len(a), len(b))
        a, b = a[:min_len], b[:min_len]
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class HybridRetriever:
    """Hybrid keyword + vector memory retriever.

    Combines keyword overlap (exact matching) with vector
    similarity (semantic matching) using a weighted fusion
    score. When no external embedding model is available,
    falls back to :class:`KeywordEmbeddingProvider`.

    Args:
        store (`MemoryStore`):
            The memory store to search.
        embedding_provider (`EmbeddingProvider | None`):
            External embedding model (e.g., sentence-transformers).
            If None, uses :class:`KeywordEmbeddingProvider`.
        keyword_weight (`float`):
            Weight for keyword score (0-1).
        vector_weight (`float`):
            Weight for vector similarity (0-1).
        min_score (`float`):
            Minimum combined score to include in results.
    """

    def __init__(
        self,
        store: MemoryStore,
        embedding_provider: EmbeddingProvider | None = None,
        keyword_weight: float = 0.4,
        vector_weight: float = 0.6,
        min_score: float = 0.01,
    ) -> None:
        """Initialize the retriever."""
        self._store = store
        self._provider = embedding_provider or KeywordEmbeddingProvider()
        self._kw_weight = keyword_weight
        self._vec_weight = vector_weight
        self._min_score = min_score
        self._cache: dict[str, list[float]] = {}

    def search(
        self,
        query: str,
        user_id: str = "",
        tenant_id: str = "",
        top_k: int = 5,
        min_confidence: float | None = None,
    ) -> list[MemoryItem]:
        """Hybrid search combining keyword and vector scores.

        Args:
            query: Search query.
            user_id: Filter by user.
            tenant_id: Filter by tenant.
            top_k: Max results.
            min_confidence: Minimum memory confidence.

        Returns:
            Ranked results by combined score.
        """
        # Get candidate memories (broader recall from store)
        candidates = self._store.list_all(
            user_id=user_id,
            tenant_id=tenant_id,
        )

        # Filter expired and low-confidence
        candidates = [
            m
            for m in candidates
            if not m.is_expired()
            and (
                min_confidence is None
                or m.confidence >= min_confidence
            )
        ]

        if not candidates:
            return []

        # Compute query embedding
        query_vec = self._provider.embed(query)

        # Score each candidate
        scored: list[tuple[float, MemoryItem]] = []
        for item in candidates:
            kw_score = item.keyword_score(query)
            item_vec = self._get_or_embed(item)
            vec_score = cosine_similarity(query_vec, item_vec)

            combined = (
                self._kw_weight * kw_score
                + self._vec_weight * vec_score
            )

            if combined >= self._min_score:
                scored.append((combined, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def _get_or_embed(self, item: MemoryItem) -> list[float]:
        """Get cached embedding or compute new one.

        Args:
            item: Memory item.

        Returns:
            Embedding vector.
        """
        if item.id not in self._cache:
            text = f"{item.content} {' '.join(item.tags)}"
            self._cache[item.id] = self._provider.embed(text)
        return self._cache[item.id]

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self._cache.clear()
