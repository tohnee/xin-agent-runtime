# -*- coding: utf-8 -*-
"""MemoryStore — in-memory CRUD + keyword search (MVP).

V2 will add Redis backend and vector search.
"""
from __future__ import annotations

from ._models import MemoryItem


class MemoryStore:
    """In-memory store for long-term memories.

    Supports multi-tenant isolation, keyword-based search,
    confidence filtering, and expiry.

    Args:
        min_confidence (`float`):
            Minimum confidence to include in search results.
    """

    def __init__(
        self,
        min_confidence: float = 0.0,
    ) -> None:
        """Initialize the store."""
        self._items: dict[str, MemoryItem] = {}
        self._min_confidence = min_confidence

    def add(self, item: MemoryItem) -> str:
        """Add or update a memory item.

        Args:
            item (`MemoryItem`): The memory to store.

        Returns:
            `str`: The memory ID.
        """
        self._items[item.id] = item
        return item.id

    def get(self, memory_id: str) -> MemoryItem | None:
        """Get a memory by ID.

        Args:
            memory_id (`str`): The memory ID.

        Returns:
            `MemoryItem | None`: The memory, or None if not found.
        """
        return self._items.get(memory_id)

    def delete(self, memory_id: str) -> bool:
        """Delete a memory.

        Args:
            memory_id (`str`): The memory ID.

        Returns:
            `bool`: True if deleted, False if not found.
        """
        return self._items.pop(memory_id, None) is not None

    def search(
        self,
        query: str,
        user_id: str = "",
        tenant_id: str = "",
        top_k: int = 5,
        min_confidence: float | None = None,
    ) -> list[MemoryItem]:
        """Search memories by keyword overlap.

        Filters by user/tenant, confidence, and expiry,
        then ranks by keyword score.

        Args:
            query (`str`): Search query.
            user_id (`str`): Filter by user.
            tenant_id (`str`): Filter by tenant.
            top_k (`int`): Max results.
            min_confidence (`float | None`): Override default
                minimum confidence threshold.

        Returns:
            `list[MemoryItem]`: Ranked results.
        """
        threshold = (
            min_confidence
            if min_confidence is not None
            else self._min_confidence
        )
        scored: list[tuple[float, MemoryItem]] = []
        for item in self._items.values():
            if item.is_expired():
                continue
            if item.confidence < threshold:
                continue
            if user_id and item.user_id != user_id:
                continue
            if tenant_id and item.tenant_id != tenant_id:
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
            user_id (`str`): Filter by user.
            tenant_id (`str`): Filter by tenant.

        Returns:
            `list[MemoryItem]`: All matching memories.
        """
        result = []
        for item in self._items.values():
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
        """Remove memories, optionally filtered by user/tenant.

        Args:
            user_id (`str`): Only clear this user's memories.
            tenant_id (`str`): Only clear this tenant's memories.

        Returns:
            `int`: Number of memories removed.
        """
        if not user_id and not tenant_id:
            count = len(self._items)
            self._items.clear()
            return count

        to_remove = [
            mid
            for mid, item in self._items.items()
            if (not user_id or item.user_id == user_id)
            and (not tenant_id or item.tenant_id == tenant_id)
        ]
        for mid in to_remove:
            del self._items[mid]
        return len(to_remove)

    @property
    def count(self) -> int:
        """Total number of stored memories."""
        return len(self._items)
