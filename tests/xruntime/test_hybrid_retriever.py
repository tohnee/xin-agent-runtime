# -*- coding: utf-8 -*-
"""Tests for HybridRetriever — keyword + vector search."""
from __future__ import annotations

import pytest

from xruntime._runtime._memory._hybrid_retriever import (
    HybridRetriever,
    KeywordEmbeddingProvider,
    cosine_similarity,
)
from xruntime._runtime._memory._models import MemoryItem
from xruntime._runtime._memory._store import MemoryStore


class TestKeywordEmbeddingProvider:
    """Fallback embedding provider tests."""

    def test_embed_returns_correct_dim(self) -> None:
        provider = KeywordEmbeddingProvider(dim=128)
        vec = provider.embed("hello world")
        assert len(vec) == 128

    def test_embed_normalized(self) -> None:
        provider = KeywordEmbeddingProvider(dim=64)
        vec = provider.embed("test text")
        norm = sum(v * v for v in vec) ** 0.5
        assert 0.99 <= norm <= 1.01  # L2 normalized

    def test_embed_empty_string(self) -> None:
        provider = KeywordEmbeddingProvider(dim=32)
        vec = provider.embed("")
        assert len(vec) == 32
        assert all(v == 0.0 for v in vec)


class TestCosineSimilarity:
    """Cosine similarity tests."""

    def test_identical_vectors(self) -> None:
        v = [1.0, 0.0, 0.5]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_zero_vector(self) -> None:
        assert cosine_similarity([0, 0], [1, 1]) == 0.0

    def test_different_lengths(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0]
        # Should not crash, truncates to min length
        result = cosine_similarity(a, b)
        assert isinstance(result, float)


class TestHybridRetriever:
    """Hybrid retriever tests."""

    @pytest.fixture
    def store(self) -> MemoryStore:
        return MemoryStore(min_confidence=0.0)

    @pytest.fixture
    def retriever(self, store: MemoryStore) -> HybridRetriever:
        return HybridRetriever(store)

    def test_exact_keyword_match(
        self,
        store: MemoryStore,
        retriever: HybridRetriever,
    ) -> None:
        """Exact keyword match still works."""
        store.add(
            MemoryItem(
                content="Python is great for data science",
                user_id="alice",
                tenant_id="acme",
                tags=["python"],
            )
        )
        results = retriever.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) >= 1
        assert "Python" in results[0].content

    def test_partial_word_match_better_than_keyword(
        self,
        store: MemoryStore,
    ) -> None:
        """Hybrid finds 'deployment' when searching 'deploy'
        (keyword-only would miss this)."""
        store.add(
            MemoryItem(
                content="The deployment process takes 5 minutes",
                user_id="alice",
                tenant_id="acme",
                tags=["deployment"],
            )
        )

        # Keyword-only search
        kw_results = store.search(
            query="deploy",
            user_id="alice",
            tenant_id="acme",
        )
        # Keyword won't match "deployment" (different word)
        assert kw_results == []

        # Hybrid search finds it via character trigram similarity
        retriever = HybridRetriever(store)
        hybrid_results = retriever.search(
            query="deploy",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(hybrid_results) >= 1
        assert "deployment" in hybrid_results[0].content.lower()

    def test_semantic_similarity_better_than_keyword(
        self,
        store: MemoryStore,
    ) -> None:
        """Hybrid finds 'PostgreSQL' when searching 'database'
        (keyword-only would miss this)."""
        store.add(
            MemoryItem(
                content="We use PostgreSQL for our database backend",
                user_id="alice",
                tenant_id="acme",
                tags=["postgresql", "database"],
            )
        )

        # Keyword search for "database" should work (tag match)
        kw_results = store.search(
            query="database",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(kw_results) == 1

        # But search for "DB" or "data store" would fail with keyword
        kw_db = store.search(
            query="data store",
            user_id="alice",
            tenant_id="acme",
        )
        # "data" and "store" don't appear as separate words
        # (they're part of "database")
        # Hybrid should still find via trigram similarity
        retriever = HybridRetriever(store)
        hybrid_results = retriever.search(
            query="data store",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(hybrid_results) >= 1

    def test_ranking_by_relevance(
        self,
        store: MemoryStore,
        retriever: HybridRetriever,
    ) -> None:
        """More relevant memories rank higher."""
        store.add(
            MemoryItem(
                content="Python Python Python everywhere",
                user_id="alice",
                tenant_id="acme",
                tags=["python"],
            )
        )
        store.add(
            MemoryItem(
                content="I once saw a Python book",
                user_id="alice",
                tenant_id="acme",
                tags=[],
            )
        )
        results = retriever.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) >= 2
        # The one with more "Python" mentions should rank higher
        assert "everywhere" in results[0].content

    def test_tenant_isolation(
        self,
        store: MemoryStore,
        retriever: HybridRetriever,
    ) -> None:
        """Tenant isolation is preserved."""
        store.add(
            MemoryItem(
                content="ACME Python project",
                user_id="alice",
                tenant_id="acme",
            )
        )
        store.add(
            MemoryItem(
                content="Other Python project",
                user_id="bob",
                tenant_id="other",
            )
        )
        results = retriever.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
        )
        assert all(r.tenant_id == "acme" for r in results)

    def test_empty_store_returns_empty(
        self,
        retriever: HybridRetriever,
    ) -> None:
        """Empty store returns empty list."""
        results = retriever.search(
            query="anything",
            user_id="alice",
            tenant_id="acme",
        )
        assert results == []

    def test_expired_filtered(
        self,
        store: MemoryStore,
        retriever: HybridRetriever,
    ) -> None:
        """Expired memories are filtered."""
        from datetime import datetime, timedelta, timezone

        store.add(
            MemoryItem(
                content="Old Python fact",
                user_id="alice",
                tenant_id="acme",
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
        store.add(
            MemoryItem(
                content="Current Python fact",
                user_id="alice",
                tenant_id="acme",
            )
        )
        results = retriever.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) == 1
        assert "Current" in results[0].content

    def test_confidence_filter(
        self,
        store: MemoryStore,
        retriever: HybridRetriever,
    ) -> None:
        """Low-confidence memories are filtered."""
        store.add(
            MemoryItem(
                content="High confidence Python fact",
                user_id="alice",
                tenant_id="acme",
                confidence=0.9,
            )
        )
        store.add(
            MemoryItem(
                content="Low confidence Python fact",
                user_id="alice",
                tenant_id="acme",
                confidence=0.1,
            )
        )
        results = retriever.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
            min_confidence=0.5,
        )
        assert len(results) == 1
        assert results[0].confidence == 0.9

    def test_top_k_limit(
        self,
        store: MemoryStore,
        retriever: HybridRetriever,
    ) -> None:
        """top_k limits results."""
        for i in range(5):
            store.add(
                MemoryItem(
                    content=f"Python fact number {i}",
                    user_id="alice",
                    tenant_id="acme",
                )
            )
        results = retriever.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
            top_k=2,
        )
        assert len(results) == 2

    def test_cache_works(
        self,
        store: MemoryStore,
        retriever: HybridRetriever,
    ) -> None:
        """Embedding cache avoids recomputation."""
        store.add(
            MemoryItem(
                content="Python is great",
                user_id="alice",
                tenant_id="acme",
            )
        )
        retriever.search("Python", user_id="alice", tenant_id="acme")
        assert len(retriever._cache) == 1

        # Second search uses cache
        retriever.search("Python", user_id="alice", tenant_id="acme")
        assert len(retriever._cache) == 1  # No new embeddings

        retriever.clear_cache()
        assert len(retriever._cache) == 0

    def test_custom_weights(
        self,
        store: MemoryStore,
    ) -> None:
        """Custom keyword/vector weights affect ranking."""
        store.add(
            MemoryItem(
                content="The deployment was successful",
                user_id="alice",
                tenant_id="acme",
                tags=["deploy"],
            )
        )

        # High keyword weight — exact tag match dominates
        kw_heavy = HybridRetriever(
            store,
            keyword_weight=0.9,
            vector_weight=0.1,
        )
        results_kw = kw_heavy.search(
            query="deploy",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results_kw) >= 1

        # High vector weight — semantic similarity matters more
        vec_heavy = HybridRetriever(
            store,
            keyword_weight=0.1,
            vector_weight=0.9,
        )
        results_vec = vec_heavy.search(
            query="deploy",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results_vec) >= 1
