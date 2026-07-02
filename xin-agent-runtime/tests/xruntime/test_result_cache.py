# -*- coding: utf-8 -*-
"""TDD tests for ResultCache (P4-A)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from xruntime._runtime._workflow._cache import (
    InMemoryResultCache,
    cache_key,
    ResultCache,
)


class TestInMemoryResultCache:
    """InMemoryResultCache — CRUD + TTL + LRU."""

    @pytest.mark.asyncio
    async def test_set_and_get_round_trip(self) -> None:
        c = InMemoryResultCache()
        await c.set("k1", "v1")
        assert await c.get("k1") == "v1"

    @pytest.mark.asyncio
    async def test_get_miss_returns_none(self) -> None:
        c = InMemoryResultCache()
        assert await c.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_invalidate(self) -> None:
        c = InMemoryResultCache()
        await c.set("k1", "v1")
        assert await c.invalidate("k1") is True
        assert await c.get("k1") is None
        assert await c.invalidate("k1") is False

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        c = InMemoryResultCache()
        await c.set("k1", "v1")
        await c.set("k2", "v2")
        count = await c.clear()
        assert count == 2
        assert await c.get("k1") is None

    @pytest.mark.asyncio
    async def test_ttl_expiry(self) -> None:
        c = InMemoryResultCache(ttl_seconds=0)
        await c.set("k1", "v1", ttl=1)
        await asyncio.sleep(0.05)
        # not expired yet (1s TTL)
        assert await c.get("k1") == "v1"
        # force expiry
        c._expires["k1"] = time.time() - 1
        assert await c.get("k1") is None

    @pytest.mark.asyncio
    async def test_lru_eviction(self) -> None:
        c = InMemoryResultCache(max_size=2)
        await c.set("k1", "v1")
        await c.set("k2", "v2")
        await c.set("k3", "v3")
        # k1 evicted (oldest)
        assert await c.get("k1") is None
        assert await c.get("k2") == "v2"
        assert await c.get("k3") == "v3"

    @pytest.mark.asyncio
    async def test_set_with_none_ttl_removes_expiry(self) -> None:
        """set with ttl=0 removes expiry (persistent)."""
        c = InMemoryResultCache(ttl_seconds=3600)
        # First set with default TTL
        await c.set("k1", "v1")
        assert "k1" in c._expires
        # Override with ttl=0 (no expiry)
        await c.set("k1", "v2", ttl=0)
        assert "k1" not in c._expires
        assert await c.get("k1") == "v2"

    @pytest.mark.asyncio
    async def test_size_property(self) -> None:
        """size property returns current entry count."""
        c = InMemoryResultCache()
        assert c.size == 0
        await c.set("k1", "v1")
        assert c.size == 1
        await c.set("k2", "v2")
        assert c.size == 2
        await c.invalidate("k1")
        assert c.size == 1


class TestCacheKeyGeneration:
    """cache_key — deterministic hashing."""

    def test_same_context_same_key(self) -> None:
        ctx = {"a": 1, "b": 2}
        k1 = cache_key("wf", "s1", ctx)
        k2 = cache_key("wf", "s1", ctx)
        assert k1 == k2

    def test_different_context_different_key(self) -> None:
        k1 = cache_key("wf", "s1", {"a": 1})
        k2 = cache_key("wf", "s1", {"a": 2})
        assert k1 != k2


class TestCacheABC:
    """ResultCache ABC raises NotImplementedError."""

    @pytest.mark.asyncio
    async def test_abc_get_raises(self) -> None:
        c = ResultCache()
        with pytest.raises(NotImplementedError):
            await c.get("x")

    @pytest.mark.asyncio
    async def test_abc_set_raises(self) -> None:
        c = ResultCache()
        with pytest.raises(NotImplementedError):
            await c.set("x", "y")

    @pytest.mark.asyncio
    async def test_abc_invalidate_raises(self) -> None:
        c = ResultCache()
        with pytest.raises(NotImplementedError):
            await c.invalidate("x")

    @pytest.mark.asyncio
    async def test_abc_clear_raises(self) -> None:
        c = ResultCache()
        with pytest.raises(NotImplementedError):
            await c.clear()
