# -*- coding: utf-8 -*-
"""Memory system models."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    """UTC now."""
    return datetime.now(timezone.utc)


class MemoryItem(BaseModel):
    """A single long-term memory entry.

    Args:
        id (`str`): Unique identifier.
        user_id (`str`): User this memory belongs to.
        tenant_id (`str`): Tenant for isolation.
        scope (`str`): Memory scope — ``user``, ``project``, ``global``.
        type (`str`): Memory type — ``preference``, ``fact``,
            ``procedure``, ``episode``.
        content (`str`): The memory content text.
        source_session_id (`str`): Session that created this memory.
        confidence (`float`): Confidence score (0.0–1.0).
        created_at (`datetime`): Creation timestamp.
        updated_at (`datetime`): Last update timestamp.
        expires_at (`datetime | None`): Optional expiry.
        tags (`list[str]`): Searchable tags.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str = ""
    tenant_id: str = ""
    scope: str = "user"
    type: str = "fact"
    content: str = ""
    source_session_id: str = ""
    confidence: float = 0.5
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    expires_at: datetime | None = None
    tags: list[str] = []

    def is_expired(self) -> bool:
        """Check if this memory has expired."""
        if self.expires_at is None:
            return False
        return _now() > self.expires_at

    def keyword_score(self, query: str) -> float:
        """Compute a simple keyword-overlap score.

        Args:
            query: The search query.

        Returns:
            Score — higher is more relevant.
        """
        query_words = set(query.lower().split())
        if not query_words:
            return 0.0
        content_words = set(self.content.lower().split())
        tag_words = {t.lower() for t in self.tags}
        all_words = content_words | tag_words
        overlap = query_words & all_words
        if not overlap:
            return 0.0
        return len(overlap) / len(query_words)
