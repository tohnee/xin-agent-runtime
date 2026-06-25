# -*- coding: utf-8 -*-
"""Knowledge registry — manages knowledge base instances.

The registry is the top-level entry point for the knowledge module.
It holds one or more :class:`KnowledgeAdapter` instances and
provides unified ``ingest`` / ``compile`` / ``retrieve`` operations
that fan out across all registered backends.

Usage::

    registry = KnowledgeRegistry()
    registry.register(LlmWikiAdapter(config=kb_config))
    await registry.initialize()

    # Ingest into all backends
    await registry.ingest("doc-1", content="...")

    # Compile all backends
    count = await registry.compile()

    # Retrieve from all backends, merge results
    result = await registry.retrieve(KnowledgeQuery(query="..."))
"""
from __future__ import annotations

import time
from typing import Any

from ._base import (
    KnowledgeBaseConfig,
    KnowledgeChunk,
    KnowledgeQuery,
    KnowledgeResult,
    KnowledgeSource,
)
from ._adapter import KnowledgeAdapter, KnowledgeAdapterFactory


def _chunk_in_scope(chunk: KnowledgeChunk, query: KnowledgeQuery) -> bool:
    """Return whether a retrieved chunk is visible to the query scope.

    Missing metadata is treated as the legacy default tenant so existing
    unscoped indexes remain readable only from the default tenant.

    Args:
        chunk (`KnowledgeChunk`): Retrieved chunk.
        query (`KnowledgeQuery`): Query containing tenant and KB scope.

    Returns:
        `bool`: True when the chunk is inside the query scope.
    """
    metadata = chunk.metadata or {}
    tenant_id = metadata.get("tenant_id", "default")
    if tenant_id != query.tenant_id:
        return False
    if query.kb_ids:
        kb_id = metadata.get("kb_id", "default")
        if kb_id not in query.kb_ids:
            return False
    return True


class KnowledgeRegistry:
    """Manages one or more knowledge base adapters.

    The registry provides a unified interface: ``ingest`` writes to
    all backends, ``retrieve`` merges results from all backends
    (sorted by score, truncated to ``top_k``).

    Args:
        factory (`KnowledgeAdapterFactory | None`):
            Adapter factory for auto-creation from config. If
            ``None``, uses the default factory.
    """

    def __init__(
        self,
        factory: KnowledgeAdapterFactory | None = None,
    ) -> None:
        """Initialize the registry."""
        self._factory = factory or KnowledgeAdapterFactory()
        self._backends: list[KnowledgeAdapter] = []
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Whether all backends are initialized."""
        return self._initialized

    @property
    def backends(self) -> list[KnowledgeAdapter]:
        """List registered backends."""
        return list(self._backends)

    def register(self, adapter: KnowledgeAdapter) -> None:
        """Register a knowledge base adapter.

        Args:
            adapter (`KnowledgeAdapter`):
                The adapter to register.
        """
        self._backends.append(adapter)
        self._initialized = False

    def register_from_config(
        self,
        config: KnowledgeBaseConfig,
    ) -> KnowledgeAdapter:
        """Create and register an adapter from config.

        Uses the factory to instantiate the correct adapter type.

        Args:
            config (`KnowledgeBaseConfig`):
                The knowledge base configuration.

        Returns:
            `KnowledgeAdapter`: The created adapter.
        """
        adapter = self._factory.create(config)
        self.register(adapter)
        return adapter

    async def initialize(self) -> None:
        """Initialize all registered backends."""
        for backend in self._backends:
            if not backend.is_initialized:
                await backend.initialize()
        self._initialized = True

    async def ingest(
        self,
        source_id: str,
        content: str,
        title: str = "",
        source_type: str = "text",
        metadata: dict[str, Any] | None = None,
    ) -> list[KnowledgeSource]:
        """Ingest a source into all backends.

        Args:
            source_id (`str`):
                Unique source identifier.
            content (`str`):
                Raw content.
            title (`str`):
                Source title.
            source_type (`str`):
                Source type.
            metadata (`dict | None`):
                Extra metadata.

        Returns:
            `list[KnowledgeSource]`: Source records from each backend.
        """
        results: list[KnowledgeSource] = []
        for backend in self._backends:
            source = await backend.ingest(
                source_id=source_id,
                content=content,
                title=title,
                source_type=source_type,
                metadata=metadata,
            )
            results.append(source)
        return results

    async def compile(self) -> int:
        """Compile all backends.

        Returns:
            `int`: Total chunks produced across all backends.
        """
        total = 0
        for backend in self._backends:
            total += await backend.compile()
        return total

    async def retrieve(
        self,
        query: KnowledgeQuery,
    ) -> KnowledgeResult:
        """Retrieve from all backends and merge.

        Results from all backends are merged, re-sorted by score,
        and truncated to ``query.top_k`` (or the default).

        Args:
            query (`KnowledgeQuery`):
                The retrieval query.

        Returns:
            `KnowledgeResult`: Merged results.
        """
        start = time.monotonic()
        all_chunks: list[KnowledgeChunk] = []
        total_found = 0

        top_k = query.top_k or 5
        min_score = query.min_score or 0.0

        for backend in self._backends:
            result = await backend.retrieve(query)
            scoped_chunks = [
                chunk
                for chunk in result.chunks
                if _chunk_in_scope(chunk, query)
            ]
            all_chunks.extend(scoped_chunks)
            total_found += len(scoped_chunks)

        merged = [c for c in all_chunks if c.score >= min_score]
        merged.sort(key=lambda c: c.score, reverse=True)
        merged = merged[:top_k]

        latency_ms = int((time.monotonic() - start) * 1000)

        return KnowledgeResult(
            query=query.query,
            chunks=merged,
            total_found=total_found,
            latency_ms=latency_ms,
        )

    async def list_sources(
        self,
        tenant_id: str = "default",
    ) -> list[KnowledgeSource]:
        """List sources from all backends.

        Args:
            tenant_id (`str`):
                Tenant scope.

        Returns:
            `list[KnowledgeSource]`: Merged source list.
        """
        sources: list[KnowledgeSource] = []
        for backend in self._backends:
            sources.extend(await backend.list_sources(tenant_id))
        return sources

    async def delete_source(self, source_id: str) -> bool:
        """Delete a source from all backends.

        Args:
            source_id (`str`):
                The source to delete.

        Returns:
            `bool`: ``True`` if deleted from any backend.
        """
        deleted = False
        for backend in self._backends:
            if await backend.delete_source(source_id):
                deleted = True
        return deleted

    async def close(self) -> None:
        """Close all backends."""
        for backend in self._backends:
            await backend.close()
        self._initialized = False
