# -*- coding: utf-8 -*-
"""ResultCache — step result caching for workflow optimization.

Caches step execution results keyed by ``(workflow_id, step_id,
context_hash)`` so that repeated calls with the same input skip
re-execution and return the cached output.

* :class:`ResultCache` — async ABC.
* :class:`InMemoryResultCache` — LRU + TTL in-memory cache.
* :func:`cache_key` — deterministic SHA-256 key generator.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any


def cache_key(
    workflow_id: str,
    step_id: str,
    context: dict[str, Any],
) -> str:
    """Generate a deterministic cache key.

    Args:
        workflow_id (`str`): The workflow id.
        step_id (`str`): The step id.
        context (`dict`): The step context.

    Returns:
        `str`: Cache key ``wf:{wf}:{step}:{hash16}``.
    """
    ctx_json = json.dumps(context, sort_keys=True, default=str)
    ctx_hash = hashlib.sha256(ctx_json.encode()).hexdigest()[:16]
    return f"wf:{workflow_id}:{step_id}:{ctx_hash}"


class ResultCache:
    """Abstract base class for step result caches."""

    async def get(self, key: str) -> str | None:
        """Look up a cached result.

        Args:
            key (`str`): The cache key.

        Returns:
            `str | None`: The cached value, or ``None``.
        """
        raise NotImplementedError

    async def set(
        self,
        key: str,
        value: str,
        ttl: int | None = None,
    ) -> None:
        """Store a result in the cache.

        Args:
            key (`str`): The cache key.
            value (`str`): The value to cache.
            ttl (`int | None`): TTL in seconds.  ``None`` = no expiry.
        """
        raise NotImplementedError

    async def invalidate(self, key: str) -> bool:
        """Remove a single entry from the cache.

        Returns:
            `bool`: ``True`` if removed, ``False`` if not found.
        """
        raise NotImplementedError

    async def clear(self) -> int:
        """Clear all entries.

        Returns:
            `int`: Number of entries removed.
        """
        raise NotImplementedError


class InMemoryResultCache(ResultCache):
    """LRU + TTL in-memory result cache.

    Args:
        max_size (`int`):
            Maximum entries.  LRU eviction when exceeded.
        ttl_seconds (`int`):
            Default TTL for entries without explicit TTL.
    """

    def __init__(
        self,
        max_size: int = 1000,
        ttl_seconds: int = 3600,
    ) -> None:
        self._store: OrderedDict[str, str] = OrderedDict()
        self._expires: dict[str, float] = {}
        self._max_size = max_size
        self._default_ttl = ttl_seconds

    async def get(self, key: str) -> str | None:
        """Look up a cached result (LRU touch + TTL check)."""
        if key not in self._store:
            return None
        exp = self._expires.get(key)
        if exp is not None and time.time() >= exp:
            self._store.pop(key, None)
            self._expires.pop(key, None)
            return None
        self._store.move_to_end(key)
        return self._store[key]

    async def set(
        self,
        key: str,
        value: str,
        ttl: int | None = None,
    ) -> None:
        """Store a value with optional TTL."""
        self._store[key] = value
        self._store.move_to_end(key)
        effective_ttl = ttl if ttl is not None else self._default_ttl
        if effective_ttl and effective_ttl > 0:
            self._expires[key] = time.time() + effective_ttl
        else:
            self._expires.pop(key, None)
        while len(self._store) > self._max_size:
            oldest_key, _ = self._store.popitem(last=False)
            self._expires.pop(oldest_key, None)

    async def invalidate(self, key: str) -> bool:
        """Remove a single entry."""
        if key not in self._store:
            return False
        self._store.pop(key, None)
        self._expires.pop(key, None)
        return True

    async def clear(self) -> int:
        """Clear all entries."""
        count = len(self._store)
        self._store.clear()
        self._expires.clear()
        return count

    @property
    def size(self) -> int:
        """Return the current number of entries."""
        return len(self._store)


__all__ = ["ResultCache", "InMemoryResultCache", "cache_key"]
