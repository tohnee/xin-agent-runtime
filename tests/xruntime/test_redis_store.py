# -*- coding: utf-8 -*-
"""Tests for RedisMemoryStore.

Redis tests require a running Redis instance and are skipped
unless RUN_REDIS_TESTS=1 is set.
"""
from __future__ import annotations

import os

import pytest

from xruntime._runtime._memory._models import MemoryItem
from xruntime._runtime._memory._redis_store import RedisMemoryStore

RUN_REDIS = os.environ.get("RUN_REDIS_TESTS") == "1"
REDIS_URL = os.environ.get(
    "REDIS_URL", "redis://localhost:6379/15",
)


@pytest.fixture
def store() -> RedisMemoryStore:
    """Fresh Redis store (uses DB 15 for testing)."""
    s = RedisMemoryStore(
        redis_url=REDIS_URL,
        key_prefix="xrt:test",
        min_confidence=0.0,
    )
    if RUN_REDIS:
        # Clean before each test
        s.clear()
    return s


@pytest.mark.skipif(
    not RUN_REDIS,
    reason="Set RUN_REDIS_TESTS=1 to run Redis store tests",
)
class TestRedisMemoryStore:
    """Redis-backed memory store tests."""

    def test_add_and_get(self, store: RedisMemoryStore) -> None:
        """Add then retrieve a memory."""
        item = MemoryItem(
            content="Python is great",
            user_id="alice",
            tenant_id="acme",
        )
        mid = store.add(item)
        retrieved = store.get(mid)
        assert retrieved is not None
        assert "Python" in retrieved.content

    def test_get_nonexistent(self, store: RedisMemoryStore) -> None:
        """Get returns None for unknown ID."""
        assert store.get("nonexistent-id") is None

    def test_search_keyword(self, store: RedisMemoryStore) -> None:
        """Search finds items by keyword."""
        store.add(
            MemoryItem(
                content="Python is great for data science",
                user_id="alice",
                tenant_id="acme",
            )
        )
        results = store.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) >= 1
        assert "Python" in results[0].content

    def test_tenant_isolation(self, store: RedisMemoryStore) -> None:
        """Tenant A can't see Tenant B's memories."""
        store.add(
            MemoryItem(
                content="ACME secret",
                user_id="alice",
                tenant_id="acme",
            )
        )
        store.add(
            MemoryItem(
                content="OtherCorp data",
                user_id="bob",
                tenant_id="othercorp",
            )
        )
        acme = store.search(
            query="secret", user_id="alice", tenant_id="acme",
        )
        other = store.search(
            query="data", user_id="bob", tenant_id="othercorp",
        )
        cross = store.search(
            query="secret", user_id="bob", tenant_id="othercorp",
        )
        assert len(acme) == 1
        assert len(other) == 1
        assert cross == []

    def test_delete(self, store: RedisMemoryStore) -> None:
        """Delete removes memory."""
        item = MemoryItem(
            content="To be deleted",
            user_id="alice",
            tenant_id="acme",
        )
        mid = store.add(item)
        assert store.delete(mid) is True
        assert store.get(mid) is None

    def test_delete_nonexistent(self, store: RedisMemoryStore) -> None:
        """Delete returns False for unknown ID."""
        assert store.delete("nonexistent") is False

    def test_clear_by_user(self, store: RedisMemoryStore) -> None:
        """Clear removes user's memories."""
        store.add(
            MemoryItem(
                content="Alice's memory",
                user_id="alice",
                tenant_id="acme",
            )
        )
        store.add(
            MemoryItem(
                content="Bob's memory",
                user_id="bob",
                tenant_id="acme",
            )
        )
        removed = store.clear(user_id="alice")
        assert removed == 1
        assert len(store.list_all(user_id="alice")) == 0
        assert len(store.list_all(user_id="bob")) == 1


class TestRedisStoreConstruction:
    """Construction tests (no Redis needed)."""

    def test_construct_default(self) -> None:
        """Can construct with defaults."""
        s = RedisMemoryStore()
        assert s._url == "redis://localhost:6379/0"
        assert s._prefix == "xrt:mem"

    def test_construct_custom(self) -> None:
        """Can construct with custom URL."""
        s = RedisMemoryStore(
            redis_url="redis://myredis:6380/2",
            key_prefix="custom:mem",
            min_confidence=0.5,
        )
        assert s._url == "redis://myredis:6380/2"
        assert s._prefix == "custom:mem"
        assert s._min_confidence == 0.5
