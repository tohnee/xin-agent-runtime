# -*- coding: utf-8 -*-
"""Tests for MemoryStore and MemoryMiddleware."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xruntime._runtime._memory._models import MemoryItem
from xruntime._runtime._memory._store import MemoryStore


class TestMemoryItem:
    """MemoryItem model tests."""

    def test_defaults(self) -> None:
        m = MemoryItem(content="test")
        assert m.id != ""
        assert m.scope == "user"
        assert m.type == "fact"
        assert m.confidence == 0.5
        assert m.expires_at is None

    def test_is_expired_false(self) -> None:
        m = MemoryItem(content="test")
        assert m.is_expired() is False

    def test_is_expired_true(self) -> None:
        m = MemoryItem(
            content="test",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert m.is_expired() is True

    def test_keyword_score_match(self) -> None:
        m = MemoryItem(
            content="User prefers Python and FastAPI",
            tags=["python", "fastapi"],
        )
        score = m.keyword_score("python fastapi")
        assert score > 0

    def test_keyword_score_no_match(self) -> None:
        m = MemoryItem(content="Hello world")
        assert m.keyword_score("python") == 0.0

    def test_keyword_score_empty_query(self) -> None:
        m = MemoryItem(content="test")
        assert m.keyword_score("") == 0.0


class TestMemoryStore:
    """MemoryStore CRUD + search tests."""

    @pytest.fixture
    def store(self) -> MemoryStore:
        return MemoryStore()

    @pytest.fixture
    def sample_memories(self) -> list[MemoryItem]:
        return [
            MemoryItem(
                content="User prefers Chinese responses",
                user_id="alice",
                tenant_id="acme",
                type="preference",
                tags=["language", "chinese"],
                confidence=0.9,
            ),
            MemoryItem(
                content="Project uses Python 3.11 and FastAPI",
                user_id="alice",
                tenant_id="acme",
                type="fact",
                tags=["python", "fastapi"],
                confidence=0.8,
            ),
            MemoryItem(
                content="Last session generated Docker deploy script",
                user_id="alice",
                tenant_id="acme",
                type="episode",
                tags=["docker", "deploy"],
                confidence=0.6,
            ),
        ]

    def test_add_and_get_memory(
        self,
        store: MemoryStore,
    ) -> None:
        """Add a memory and retrieve by ID."""
        m = MemoryItem(
            content="test content",
            user_id="alice",
            tenant_id="acme",
        )
        mem_id = store.add(m)
        retrieved = store.get(mem_id)
        assert retrieved is not None
        assert retrieved.content == "test content"

    def test_get_nonexistent(self, store: MemoryStore) -> None:
        """get() returns None for unknown ID."""
        assert store.get("nonexistent-id") is None

    def test_search_keyword_match(
        self,
        store: MemoryStore,
        sample_memories: list[MemoryItem],
    ) -> None:
        """Search finds memories matching keywords."""
        for m in sample_memories:
            store.add(m)
        results = store.search(
            query="Python FastAPI",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) > 0
        assert any("Python" in r.content for r in results)

    def test_search_no_match(
        self,
        store: MemoryStore,
        sample_memories: list[MemoryItem],
    ) -> None:
        """Search with no matching keywords returns empty."""
        for m in sample_memories:
            store.add(m)
        results = store.search(
            query="kubernetes",
            user_id="alice",
            tenant_id="acme",
        )
        assert results == []

    def test_search_top_k(
        self,
        store: MemoryStore,
        sample_memories: list[MemoryItem],
    ) -> None:
        """top_k limits the number of results."""
        for m in sample_memories:
            store.add(m)
        results = store.search(
            query="Python FastAPI Chinese Docker",
            user_id="alice",
            tenant_id="acme",
            top_k=1,
        )
        assert len(results) == 1

    def test_delete_memory(
        self,
        store: MemoryStore,
    ) -> None:
        """Delete removes a memory."""
        m = MemoryItem(content="to delete", user_id="alice")
        mem_id = store.add(m)
        assert store.delete(mem_id) is True
        assert store.get(mem_id) is None

    def test_delete_nonexistent(self, store: MemoryStore) -> None:
        """Delete returns False for unknown ID."""
        assert store.delete("nonexistent") is False

    def test_tenant_isolation(
        self,
        store: MemoryStore,
    ) -> None:
        """Memories are isolated by tenant."""
        store.add(
            MemoryItem(
                content="acme secret",
                user_id="alice",
                tenant_id="acme",
            ),
        )
        store.add(
            MemoryItem(
                content="other secret",
                user_id="bob",
                tenant_id="other",
            ),
        )
        results = store.search(
            query="secret",
            user_id="alice",
            tenant_id="acme",
        )
        assert all(r.tenant_id == "acme" for r in results)
        assert len(results) == 1

    def test_expired_memory_filtered(
        self,
        store: MemoryStore,
    ) -> None:
        """Expired memories are filtered from search."""
        store.add(
            MemoryItem(
                content="old expired fact about Python",
                user_id="alice",
                tenant_id="acme",
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ),
        )
        store.add(
            MemoryItem(
                content="current fact about Python",
                user_id="alice",
                tenant_id="acme",
            ),
        )
        results = store.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) == 1
        assert "current" in results[0].content

    def test_confidence_filter(
        self,
        store: MemoryStore,
    ) -> None:
        """Low-confidence memories are filtered."""
        store.add(
            MemoryItem(
                content="high confidence Python fact",
                user_id="alice",
                tenant_id="acme",
                confidence=0.9,
            ),
        )
        store.add(
            MemoryItem(
                content="low confidence Python fact",
                user_id="alice",
                tenant_id="acme",
                confidence=0.1,
            ),
        )
        results = store.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
            min_confidence=0.5,
        )
        assert len(results) == 1
        assert results[0].confidence == 0.9

    def test_search_sorted_by_score(
        self,
        store: MemoryStore,
    ) -> None:
        """Results are sorted by relevance score (descending)."""
        store.add(
            MemoryItem(
                content="Python Python Python",
                user_id="alice",
                tenant_id="acme",
            ),
        )
        store.add(
            MemoryItem(
                content="Python is mentioned once here",
                user_id="alice",
                tenant_id="acme",
            ),
        )
        results = store.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) >= 2
        # Higher score first
        assert results[0].keyword_score("Python") >= results[1].keyword_score(
            "Python",
        )

    def test_clear_user_memories(
        self,
        store: MemoryStore,
    ) -> None:
        """clear() removes all memories for a user."""
        store.add(
            MemoryItem(
                content="mem1",
                user_id="alice",
                tenant_id="acme",
            ),
        )
        store.add(
            MemoryItem(
                content="mem2",
                user_id="alice",
                tenant_id="acme",
            ),
        )
        count = store.clear(user_id="alice", tenant_id="acme")
        assert count == 2
        assert store.search("mem", user_id="alice", tenant_id="acme") == []
