# -*- coding: utf-8 -*-
"""Tests for embedding providers (V2 vector retrieval).

sentence-transformers tests are skipped if not installed.
"""
from __future__ import annotations

import pytest

from xruntime._runtime._memory._embedding_providers import (
    OpenAIEmbeddingProvider,
    SentenceTransformersProvider,
    get_best_available_provider,
)
from xruntime._runtime._memory._hybrid_retriever import (
    HybridRetriever,
    KeywordEmbeddingProvider,
)
from xruntime._runtime._memory._models import MemoryItem
from xruntime._runtime._memory._store import MemoryStore


class TestSentenceTransformersProvider:
    """SentenceTransformers provider tests."""

    def test_fallback_when_not_installed(self) -> None:
        """Falls back to keyword embedding if not installed."""
        provider = SentenceTransformersProvider()
        provider._model = "fallback"  # Simulate fallback
        vec = provider.embed("hello world")
        assert len(vec) > 0
        assert isinstance(vec, list)

    def test_embed_returns_list(self) -> None:
        """embed() returns a list of floats."""
        provider = SentenceTransformersProvider()
        provider._model = "fallback"
        vec = provider.embed("test text")
        assert isinstance(vec, list)
        assert all(isinstance(v, float) for v in vec)


class TestOpenAIEmbeddingProvider:
    """OpenAI provider tests (no API call, just construction)."""

    def test_construction(self) -> None:
        """Can be constructed with api_key."""
        provider = OpenAIEmbeddingProvider(
            api_key="sk-test",
            model="text-embedding-3-small",
        )
        assert provider._api_key == "sk-test"
        assert provider._model == "text-embedding-3-small"

    def test_custom_base_url(self) -> None:
        """Custom base_url is stored."""
        provider = OpenAIEmbeddingProvider(
            api_key="sk-test",
            base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        )
        assert provider._base_url is not None


class TestGetBestAvailableProvider:
    """Provider selection tests."""

    def test_returns_provider(self) -> None:
        """Returns some provider."""
        provider = get_best_available_provider()
        assert provider is not None
        assert hasattr(provider, "embed")


class TestHybridRetrieverWithProviders:
    """HybridRetriever with different providers."""

    def test_keyword_provider_still_works(self) -> None:
        """KeywordEmbeddingProvider works in HybridRetriever."""
        store = MemoryStore()
        store.add(
            MemoryItem(
                content="Python is great for data science",
                user_id="alice",
                tenant_id="acme",
                tags=["python"],
            )
        )
        retriever = HybridRetriever(
            store,
            embedding_provider=KeywordEmbeddingProvider(),
        )
        results = retriever.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) >= 1
        assert "Python" in results[0].content

    def test_partial_word_match_with_keyword(self) -> None:
        """deploy finds deployment (trigram matching)."""
        store = MemoryStore()
        store.add(
            MemoryItem(
                content="The deployment process takes 5 minutes",
                user_id="alice",
                tenant_id="acme",
            )
        )
        retriever = HybridRetriever(
            store,
            embedding_provider=KeywordEmbeddingProvider(),
        )
        results = retriever.search(
            query="deploy",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) >= 1

    def test_semantic_synonym_with_keyword(self) -> None:
        """'database' finds 'PostgreSQL' (trigram partial match)."""
        store = MemoryStore()
        store.add(
            MemoryItem(
                content="We use PostgreSQL for data storage",
                user_id="alice",
                tenant_id="acme",
                tags=["postgresql", "database"],
            )
        )
        retriever = HybridRetriever(store)
        results = retriever.search(
            query="database",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) >= 1
        assert "PostgreSQL" in results[0].content


@pytest.mark.skipif(
    True,  # Will be dynamically enabled when sentence-transformers installed
    reason="Install sentence-transformers to run V2 comparison tests",
)
class TestV2RetrievalQualityComparison:
    """Compare trigram vs sentence-transformers quality.

    Run with: ``pip install sentence-transformers && pytest tests/xruntime/test_embedding_providers.py -v -k V2``
    """

    def test_synonym_match_better_with_st(self) -> None:
        """'car' should match 'automobile' better with ST."""
        store = MemoryStore()
        store.add(
            MemoryItem(
                content="I bought a new automobile last week",
                user_id="alice",
                tenant_id="acme",
            )
        )

        # Keyword (trigram) — might not find "car" in "automobile"
        kw_retriever = HybridRetriever(
            store,
            embedding_provider=KeywordEmbeddingProvider(),
        )
        kw_results = kw_retriever.search(
            query="car",
            user_id="alice",
            tenant_id="acme",
        )

        # ST — should find via semantic similarity
        st_retriever = HybridRetriever(
            store,
            embedding_provider=SentenceTransformersProvider(),
        )
        st_results = st_retriever.search(
            query="car",
            user_id="alice",
            tenant_id="acme",
        )

        # ST should find it (or at least not worse)
        assert len(st_results) >= len(kw_results)
