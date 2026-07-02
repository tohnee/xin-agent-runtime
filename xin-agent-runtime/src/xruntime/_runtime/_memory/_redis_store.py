# -*- coding: utf-8 -*-
"""RedisMemoryStore — Redis-backed persistent memory store.

Same interface as MemoryStore (add/get/search/delete/clear/count).
Uses Redis hash for items + keyword index for search.

Requires: ``pip install redis``

Args:
    redis_url (`str`):
        Redis connection URL (e.g. ``redis://localhost:6379/0``).
    key_prefix (`str`):
        Redis key prefix for namespacing.
    min_confidence (`float`):
        Minimum confidence to include in search results.
"""
from __future__ import annotations

import logging
from typing import Any

from ._models import MemoryItem

logger = logging.getLogger("xruntime.memory.redis_store")


class RedisMemoryStore:
    """Redis-backed persistent memory store.

    Implements the same interface as :class:`MemoryStore` so it
    can be used as a drop-in replacement.

    Stores items as Redis hashes:
        ``xrt:mem:{id}`` → JSON serialized MemoryItem

    Keyword index:
        ``xrt:mem:kw:{word}`` → SET of item IDs containing word

    User/tenant index:
        ``xrt:mem:user:{user_id}`` → SET of item IDs
        ``xrt:mem:tenant:{tenant_id}`` → SET of item IDs
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "xrt:mem",
        min_confidence: float = 0.0,
    ) -> None:
        """Initialize the store."""
        self._url = redis_url
        self._prefix = key_prefix
        self._min_confidence = min_confidence
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazily create Redis client."""
        if self._client is not None:
            return self._client
        import redis

        self._client = redis.from_url(
            self._url,
            decode_responses=True,
        )
        return self._client

    def _item_key(self, item_id: str) -> str:
        """Redis key for an item."""
        return f"{self._prefix}:{item_id}"

    def _kw_key(self, word: str) -> str:
        """Redis key for a keyword index."""
        return f"{self._prefix}:kw:{word}"

    def _user_key(self, user_id: str) -> str:
        """Redis key for user index."""
        return f"{self._prefix}:user:{user_id}"

    def _tenant_key(self, tenant_id: str) -> str:
        """Redis key for tenant index."""
        return f"{self._prefix}:tenant:{tenant_id}"

    def add(self, item: MemoryItem) -> str:
        """Add or update a memory item.

        Args:
            item: The memory to store.

        Returns:
            `str`: The memory ID.
        """
        client = self._get_client()
        key = self._item_key(item.id)
        client.set(key, item.model_dump_json())

        # Index keywords from content + tags
        words = set(item.content.lower().split())
        words.update(t.lower() for t in item.tags)
        pipe = client.pipeline()
        for word in words:
            pipe.sadd(self._kw_key(word), item.id)
        if item.user_id:
            pipe.sadd(self._user_key(item.user_id), item.id)
        if item.tenant_id:
            pipe.sadd(self._tenant_key(item.tenant_id), item.id)
        pipe.execute()
        return item.id

    def get(self, memory_id: str) -> MemoryItem | None:
        """Get a memory by ID.

        Args:
            memory_id: The memory ID.

        Returns:
            `MemoryItem | None`: The memory, or None if not found.
        """
        client = self._get_client()
        data = client.get(self._item_key(memory_id))
        if data is None:
            return None
        return MemoryItem.model_validate_json(data)

    def delete(self, memory_id: str) -> bool:
        """Delete a memory.

        Args:
            memory_id: The memory ID.

        Returns:
            `bool`: True if deleted, False if not found.
        """
        client = self._get_client()
        item = self.get(memory_id)
        if item is None:
            return False

        pipe = client.pipeline()
        pipe.delete(self._item_key(memory_id))

        # Remove from keyword indices
        words = set(item.content.lower().split())
        words.update(t.lower() for t in item.tags)
        for word in words:
            pipe.srem(self._kw_key(word), memory_id)
        if item.user_id:
            pipe.srem(self._user_key(item.user_id), memory_id)
        if item.tenant_id:
            pipe.srem(self._tenant_key(item.tenant_id), memory_id)
        pipe.execute()
        return True

    def search(
        self,
        query: str,
        user_id: str = "",
        tenant_id: str = "",
        top_k: int = 5,
        min_confidence: float | None = None,
    ) -> list[MemoryItem]:
        """Search memories by keyword overlap.

        Args:
            query: Search query.
            user_id: Filter by user.
            tenant_id: Filter by tenant.
            top_k: Max results.
            min_confidence: Override default confidence threshold.

        Returns:
            `list[MemoryItem]`: Ranked results.
        """
        client = self._get_client()
        threshold = (
            min_confidence
            if min_confidence is not None
            else self._min_confidence
        )

        query_words = set(query.lower().split())
        if not query_words:
            return []

        # Find candidate IDs from keyword index
        candidate_ids: set[str] = set()
        for word in query_words:
            ids = client.smembers(self._kw_key(word))
            candidate_ids.update(ids)

        if not candidate_ids:
            return []

        # Filter by user/tenant
        if user_id:
            user_ids = set(client.smembers(self._user_key(user_id)))
            candidate_ids &= user_ids
        if tenant_id:
            tenant_ids = set(client.smembers(self._tenant_key(tenant_id)))
            candidate_ids &= tenant_ids

        if not candidate_ids:
            return []

        # Load items and score
        scored: list[tuple[float, MemoryItem]] = []
        for item_id in candidate_ids:
            data = client.get(self._item_key(item_id))
            if data is None:
                continue
            item = MemoryItem.model_validate_json(data)
            if item.is_expired():
                continue
            if item.confidence < threshold:
                continue
            score = item.keyword_score(query)
            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def list_all(
        self,
        user_id: str = "",
        tenant_id: str = "",
    ) -> list[MemoryItem]:
        """List all memories (optionally filtered).

        Args:
            user_id: Filter by user.
            tenant_id: Filter by tenant.

        Returns:
            `list[MemoryItem]`: All matching memories.
        """
        client = self._get_client()

        if user_id:
            ids = client.smembers(self._user_key(user_id))
        elif tenant_id:
            ids = client.smembers(self._tenant_key(tenant_id))
        else:
            # Scan all items (less efficient)
            ids = set()
            for key in client.scan_iter(f"{self._prefix}:*"):
                key_str = key.removeprefix(f"{self._prefix}:")
                if ":" not in key_str and key_str:
                    ids.add(key_str)

        result: list[MemoryItem] = []
        for item_id in ids:
            data = client.get(self._item_key(item_id))
            if data is None:
                continue
            item = MemoryItem.model_validate_json(data)
            if user_id and item.user_id != user_id:
                continue
            if tenant_id and item.tenant_id != tenant_id:
                continue
            result.append(item)
        return result

    def clear(
        self,
        user_id: str = "",
        tenant_id: str = "",
    ) -> int:
        """Remove memories, optionally filtered.

        Args:
            user_id: Only clear this user's memories.
            tenant_id: Only clear this tenant's memories.

        Returns:
            `int`: Number of memories removed.
        """
        items = self.list_all(
            user_id=user_id,
            tenant_id=tenant_id,
        )
        count = 0
        for item in items:
            if self.delete(item.id):
                count += 1
        return count

    @property
    def count(self) -> int:
        """Total number of stored memories."""
        client = self._get_client()
        total = 0
        for key in client.scan_iter(f"{self._prefix}:*"):
            key_str = key
            # Only count item keys (not index keys)
            if (
                ":kw:" not in key_str
                and ":user:" not in key_str
                and ":tenant:" not in key_str
            ):
                total += 1
        return total
