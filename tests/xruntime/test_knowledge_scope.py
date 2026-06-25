# -*- coding: utf-8 -*-
"""Tests for tenant/KB scoped knowledge retrieval."""

from typing import Any

import pytest

from xruntime._runtime._knowledge._adapter import KnowledgeAdapter
from xruntime._runtime._knowledge._base import (
    KnowledgeBaseConfig,
    KnowledgeChunk,
    KnowledgeQuery,
    KnowledgeResult,
)
from xruntime._runtime._knowledge._registry import KnowledgeRegistry

pytestmark = pytest.mark.anyio


class _ScopedBackend(KnowledgeAdapter):
    """Backend stub returning a fixed set of scoped chunks."""

    @classmethod
    def backend_name(cls) -> str:
        return "scoped"

    async def initialize(self) -> None:
        self._initialized = True

    async def ingest(self, **kwargs: Any):
        raise NotImplementedError

    async def compile(self) -> int:
        return 0

    async def retrieve(self, query: KnowledgeQuery) -> KnowledgeResult:
        return KnowledgeResult(
            query=query.query,
            chunks=[
                KnowledgeChunk(
                    chunk_id="acme-public",
                    source_id="s1",
                    title="Allowed",
                    content="allowed content",
                    score=0.9,
                    metadata={"tenant_id": "acme", "kb_id": "public"},
                ),
                KnowledgeChunk(
                    chunk_id="acme-admin",
                    source_id="s2",
                    title="Admin",
                    content="admin content",
                    score=0.8,
                    metadata={"tenant_id": "acme", "kb_id": "admin"},
                ),
                KnowledgeChunk(
                    chunk_id="other-public",
                    source_id="s3",
                    title="Other",
                    content="other tenant content",
                    score=0.7,
                    metadata={"tenant_id": "other", "kb_id": "public"},
                ),
            ],
            total_found=3,
        )

    async def list_sources(self, tenant_id: str = "default"):
        return []

    async def delete_source(self, source_id: str) -> bool:
        return False


async def test_registry_filters_by_tenant_and_authorized_kbs() -> None:
    """Registry retrieval must not return chunks outside query scope."""
    registry = KnowledgeRegistry()
    registry.register(_ScopedBackend(KnowledgeBaseConfig()))
    await registry.initialize()

    result = await registry.retrieve(
        KnowledgeQuery(
            query="policy",
            tenant_id="acme",
            user_id="viewer-1",
            kb_ids=["public"],
        )
    )

    assert [chunk.chunk_id for chunk in result.chunks] == ["acme-public"]
    assert result.total_found == 1


async def test_empty_kb_scope_keeps_tenant_filter_only() -> None:
    """Empty kb_ids means all KBs in the tenant are eligible."""
    registry = KnowledgeRegistry()
    registry.register(_ScopedBackend(KnowledgeBaseConfig()))
    await registry.initialize()

    result = await registry.retrieve(
        KnowledgeQuery(
            query="policy",
            tenant_id="acme",
            user_id="admin-1",
        )
    )

    assert [chunk.chunk_id for chunk in result.chunks] == [
        "acme-public",
        "acme-admin",
    ]
    assert result.total_found == 2


async def test_llm_wiki_preserves_source_scope_metadata(tmp_path) -> None:
    """LLM-Wiki chunks should carry tenant and KB metadata."""
    from xruntime._runtime._knowledge._llm_wiki_adapter import (
        LlmWikiAdapter,
    )

    adapter = LlmWikiAdapter(
        KnowledgeBaseConfig(
            raw_dir=str(tmp_path / "raw"),
            compiled_dir=str(tmp_path / "compiled"),
        )
    )
    await adapter.initialize()
    await adapter.ingest(
        source_id="doc1",
        content="# Billing\nBilling policy applies to invoices.",
        title="Billing Guide",
        metadata={"tenant_id": "acme", "kb_id": "finance"},
    )
    await adapter.compile()

    result = await adapter.retrieve(
        KnowledgeQuery(
            query="billing policy",
            tenant_id="acme",
            kb_ids=["finance"],
        )
    )

    assert result.total_found == 1
    assert result.chunks[0].metadata["tenant_id"] == "acme"
    assert result.chunks[0].metadata["kb_id"] == "finance"


async def test_knowledge_middleware_passes_user_and_kb_scope() -> None:
    """Static injection should query with user and authorized KB scope."""
    from agentscope.event import ReplyStartEvent
    from agentscope.message import UserMsg
    from xruntime._runtime._knowledge._middleware import KnowledgeMiddleware

    class _Registry:
        last_query: KnowledgeQuery | None = None

        async def retrieve(self, query: KnowledgeQuery) -> KnowledgeResult:
            self.last_query = query
            return KnowledgeResult(query=query.query)

    registry = _Registry()
    mw = KnowledgeMiddleware(
        registry=registry,  # type: ignore[arg-type]
        tenant_id="acme",
        user_id="viewer-1",
        kb_ids=["public"],
    )

    class _Agent:
        class _State:
            context: list[Any] = []

        state = _State()

    async def next_handler(**kwargs: Any):
        yield ReplyStartEvent(session_id="s", reply_id="r", name="agent")

    async for _ in mw.on_reply(
        agent=_Agent(),
        input_kwargs={"inputs": UserMsg(name="user", content="hello")},
        next_handler=next_handler,
    ):
        pass

    assert registry.last_query is not None
    assert registry.last_query.tenant_id == "acme"
    assert registry.last_query.user_id == "viewer-1"
    assert registry.last_query.kb_ids == ["public"]
