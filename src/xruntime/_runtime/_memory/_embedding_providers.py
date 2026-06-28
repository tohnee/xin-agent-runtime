# -*- coding: utf-8 -*-
"""Embedding providers for V2 vector retrieval.

Provides pluggable embedding models:
    - SentenceTransformersProvider: uses sentence-transformers (lazy import)
    - OpenAIEmbeddingProvider: uses OpenAI embedding API
    - KeywordEmbeddingProvider: zero-dependency fallback (trigram hash)

All providers implement the EmbeddingProvider protocol from
_hybrid_retriever.py: ``embed(text: str) -> list[float]``.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("xruntime.memory.embeddings")


class SentenceTransformersProvider:
    """Embedding provider using sentence-transformers.

    Uses ``all-MiniLM-L6-v2`` by default (384 dims, fast, good quality).
    Falls back to KeywordEmbeddingProvider if sentence-transformers
    is not installed.

    Args:
        model_name (`str`):
            HuggingFace model name.
        cache_folder (`str | None`):
            Optional cache folder for downloaded models.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        cache_folder: str | None = None,
    ) -> None:
        """Initialize the provider."""
        self._model_name = model_name
        self._cache_folder = cache_folder
        self._model: Any = None
        self._dim: int = 0

    def _ensure_model(self) -> None:
        """Lazily load the model on first use."""
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self._model_name,
                cache_folder=self._cache_folder,
            )
            self._dim = self._model.get_sentence_embedding_dimension()
            logger.info(
                "Loaded sentence-transformers model: %s (dim=%d)",
                self._model_name,
                self._dim,
            )
        except ImportError:
            logger.warning(
                "sentence-transformers not installed, "
                "falling back to keyword embedding",
            )
            self._model = "fallback"

    def embed(self, text: str) -> list[float]:
        """Embed text using sentence-transformers.

        Args:
            text: Input text.

        Returns:
            Embedding vector.
        """
        self._ensure_model()
        if self._model == "fallback":
            from ._hybrid_retriever import KeywordEmbeddingProvider

            return KeywordEmbeddingProvider().embed(text)
        vec = self._model.encode(text, normalize_embeddings=True)
        return vec.tolist()


class OpenAIEmbeddingProvider:
    """Embedding provider using OpenAI API.

    Args:
        api_key (`str`):
            OpenAI API key.
        model (`str`):
            Embedding model name (default: text-embedding-3-small).
        base_url (`str | None`):
            Custom base URL (for compatible endpoints).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str | None = None,
    ) -> None:
        """Initialize the provider."""
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._client: Any = None

    def _ensure_client(self) -> None:
        """Lazily create OpenAI client."""
        if self._client is not None:
            return
        from openai import OpenAI

        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = OpenAI(**kwargs)

    def embed(self, text: str) -> list[float]:
        """Embed text using OpenAI API.

        Args:
            text: Input text.

        Returns:
            Embedding vector.
        """
        self._ensure_client()
        response = self._client.embeddings.create(
            input=text,
            model=self._model,
        )
        return response.data[0].embedding


def get_best_available_provider() -> Any:
    """Get the best available embedding provider.

    Tries sentence-transformers first, falls back to keyword.

    Returns:
        `Any`: An EmbeddingProvider instance.
    """
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401

        return SentenceTransformersProvider()
    except ImportError:
        from ._hybrid_retriever import KeywordEmbeddingProvider

        logger.info(
            "sentence-transformers not available, "
            "using KeywordEmbeddingProvider",
        )
        return KeywordEmbeddingProvider()
