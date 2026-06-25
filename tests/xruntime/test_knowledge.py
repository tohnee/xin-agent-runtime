# -*- coding: utf-8 -*-
"""Tests for the knowledge base framework."""
import os
import tempfile
from typing import Any

import pytest

from xruntime._runtime._knowledge._base import (
    KnowledgeBaseConfig,
    KnowledgeChunk,
    KnowledgeQuery,
    KnowledgeResult,
)
from xruntime._runtime._knowledge._adapter import (
    KnowledgeAdapter,
    KnowledgeAdapterFactory,
)
from xruntime._runtime._knowledge._registry import KnowledgeRegistry
from xruntime._runtime._knowledge._llm_wiki_adapter import (
    LlmWikiAdapter,
    _register_default_adapter,
)


class TestKnowledgeBaseConfig:
    """Tests for KnowledgeBaseConfig."""

    def test_defaults(self) -> None:
        """Default config should have sensible values."""
        config = KnowledgeBaseConfig()
        assert config.backend == "llm_wiki"
        assert config.retrieval_top_k == 5
        assert config.auto_compile is False

    def test_custom_values(self) -> None:
        """Should accept custom values."""
        config = KnowledgeBaseConfig(
            backend="vector_store",
            raw_dir="/data/raw",
            compiled_dir="/data/compiled",
            retrieval_top_k=10,
            auto_compile=True,
        )
        assert config.backend == "vector_store"
        assert config.retrieval_top_k == 10


class TestKnowledgeResult:
    """Tests for KnowledgeResult."""

    def test_to_context_text_empty(self) -> None:
        """Empty result should produce empty context."""
        result = KnowledgeResult(query="test")
        assert result.to_context_text() == ""

    def test_to_context_text_with_chunks(self) -> None:
        """Should format chunks as context text."""
        result = KnowledgeResult(
            query="test",
            chunks=[
                KnowledgeChunk(
                    chunk_id="c1",
                    source_id="s1",
                    title="Section A",
                    content="Content of A",
                ),
                KnowledgeChunk(
                    chunk_id="c2",
                    source_id="s1",
                    title="Section B",
                    content="Content of B",
                ),
            ],
        )
        text = result.to_context_text()
        assert "[1] Section A" in text
        assert "Content of A" in text
        assert "[2] Section B" in text


class TestKnowledgeAdapterFactory:
    """Tests for KnowledgeAdapterFactory."""

    def test_register_and_create(self) -> None:
        """Should register and create adapters."""
        factory = KnowledgeAdapterFactory()

        @factory.register("test_backend")
        class TestAdapter(KnowledgeAdapter):
            @classmethod
            def backend_name(cls) -> str:
                return "test_backend"

            async def initialize(self) -> None:
                self._initialized = True

            async def ingest(self, **kwargs):
                pass

            async def compile(self) -> int:
                return 0

            async def retrieve(self, query):
                return KnowledgeResult(query=query.query)

            async def list_sources(self, tenant_id="default"):
                return []

            async def delete_source(self, source_id):
                return False

        adapter = factory.create(
            KnowledgeBaseConfig(backend="test_backend"),
        )
        assert isinstance(adapter, TestAdapter)

    def test_unregistered_backend_raises(self) -> None:
        """Unregistered backend should raise ValueError."""
        factory = KnowledgeAdapterFactory()
        with pytest.raises(ValueError, match="No knowledge adapter"):
            factory.create(
                KnowledgeBaseConfig(backend="nonexistent"),
            )

    def test_registered_backends(self) -> None:
        """Should list registered backends."""
        factory = KnowledgeAdapterFactory()

        @factory.register("a")
        class AdapterA(KnowledgeAdapter):
            async def initialize(self):
                pass

            async def ingest(self, **kw):
                pass

            async def compile(self):
                return 0

            async def retrieve(self, q):
                return KnowledgeResult()

            async def list_sources(self, **kw):
                return []

            async def delete_source(self, sid):
                return False

        assert "a" in factory.registered_backends


class TestLlmWikiAdapter:
    """Tests for the LLM-Wiki adapter."""

    def test_backend_name(self) -> None:
        """Should return correct backend name."""
        assert LlmWikiAdapter.backend_name() == "llm_wiki"

    async def test_initialize_creates_dirs(self) -> None:
        """initialize() should create directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = KnowledgeBaseConfig(
                backend="llm_wiki",
                raw_dir=os.path.join(tmpdir, "raw"),
                compiled_dir=os.path.join(tmpdir, "compiled"),
            )
            adapter = LlmWikiAdapter(config)
            await adapter.initialize()
            assert os.path.exists(config.raw_dir)
            assert os.path.exists(config.compiled_dir)
            assert adapter.is_initialized

    async def test_ingest_and_retrieve(self) -> None:
        """Should ingest, compile, and retrieve."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = KnowledgeBaseConfig(
                backend="llm_wiki",
                raw_dir=os.path.join(tmpdir, "raw"),
                compiled_dir=os.path.join(tmpdir, "compiled"),
            )
            adapter = LlmWikiAdapter(config)
            await adapter.initialize()

            await adapter.ingest(
                source_id="doc1",
                content="# Python Basics\nPython is a language.\n"
                "# Data Types\nPython has int, str, float.",
                title="Python Guide",
            )

            count = await adapter.compile()
            assert count > 0

            result = await adapter.retrieve(
                KnowledgeQuery(query="python data types"),
            )
            assert result.total_found > 0
            assert any(
                "data" in c.title.lower() or "type" in c.title.lower()
                for c in result.chunks
            )

    async def test_delete_source(self) -> None:
        """Should delete source and compiled chunks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = KnowledgeBaseConfig(
                backend="llm_wiki",
                raw_dir=os.path.join(tmpdir, "raw"),
                compiled_dir=os.path.join(tmpdir, "compiled"),
            )
            adapter = LlmWikiAdapter(config)
            await adapter.initialize()

            await adapter.ingest(
                source_id="doc1",
                content="Test content here",
                title="Test",
            )
            await adapter.compile()

            deleted = await adapter.delete_source("doc1")
            assert deleted is True

            sources = await adapter.list_sources()
            assert len(sources) == 0

    async def test_auto_compile(self) -> None:
        """auto_compile should trigger compilation on ingest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = KnowledgeBaseConfig(
                backend="llm_wiki",
                raw_dir=os.path.join(tmpdir, "raw"),
                compiled_dir=os.path.join(tmpdir, "compiled"),
                auto_compile=True,
            )
            adapter = LlmWikiAdapter(config)
            await adapter.initialize()

            await adapter.ingest(
                source_id="doc1",
                content="Auto compile test content",
                title="Auto",
            )

            result = await adapter.retrieve(
                KnowledgeQuery(query="auto compile"),
            )
            assert result.total_found > 0


class TestKnowledgeRegistry:
    """Tests for KnowledgeRegistry."""

    async def test_register_and_retrieve(self) -> None:
        """Registry should merge results from backends."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = KnowledgeBaseConfig(
                backend="llm_wiki",
                raw_dir=os.path.join(tmpdir, "raw"),
                compiled_dir=os.path.join(tmpdir, "compiled"),
            )
            registry = KnowledgeRegistry()
            registry.register(LlmWikiAdapter(config))
            await registry.initialize()

            await registry.ingest(
                "doc1",
                content="# API Design\nREST API patterns",
                title="API Guide",
            )
            await registry.compile()

            result = await registry.retrieve(
                KnowledgeQuery(query="api design"),
            )
            assert len(result.chunks) > 0

    async def test_close(self) -> None:
        """close() should mark as not initialized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = KnowledgeBaseConfig(
                backend="llm_wiki",
                raw_dir=os.path.join(tmpdir, "raw"),
                compiled_dir=os.path.join(tmpdir, "compiled"),
            )
            registry = KnowledgeRegistry()
            registry.register(LlmWikiAdapter(config))
            await registry.initialize()
            assert registry.is_initialized

            await registry.close()
            assert not registry.is_initialized


class TestDefaultFactoryRegistration:
    """Tests for default factory registration."""

    def test_register_default_adapter(self) -> None:
        """Should register llm_wiki with default factory."""
        _register_default_adapter()
        from xruntime._runtime._knowledge._adapter import (
            get_default_factory,
        )

        factory = get_default_factory()
        assert "llm_wiki" in factory.registered_backends


class _FakeRegistry:
    """A minimal registry stub returning a fixed retrieval result."""

    def __init__(self, result: KnowledgeResult) -> None:
        self._result = result
        self.last_query: KnowledgeQuery | None = None

    async def retrieve(self, query: KnowledgeQuery) -> KnowledgeResult:
        self.last_query = query
        return self._result


class _FakeState:
    """A stand-in for ``agent.state`` exposing a mutable context list."""

    def __init__(self) -> None:
        self.context: list[Any] = []


class _FakeAgent:
    """A stand-in agent exposing only ``state.context``."""

    def __init__(self) -> None:
        self.state = _FakeState()


def _result_with_chunk() -> KnowledgeResult:
    """Build a non-empty retrieval result for injection tests."""
    return KnowledgeResult(
        query="q",
        chunks=[
            KnowledgeChunk(
                chunk_id="c1",
                source_id="s1",
                title="Section A",
                content="Relevant content",
                score=0.9,
            ),
        ],
        total_found=1,
    )


class TestExtractQueryText:
    """Tests for the ``_extract_query_text`` helper."""

    def test_none_inputs(self) -> None:
        """None inputs yield empty string."""
        from xruntime._runtime._knowledge._middleware import (
            _extract_query_text,
        )

        assert _extract_query_text(None) == ""

    def test_single_user_msg(self) -> None:
        """A single user message yields its text."""
        from agentscope.message import UserMsg
        from xruntime._runtime._knowledge._middleware import (
            _extract_query_text,
        )

        msg = UserMsg(name="user", content="hello world")
        assert _extract_query_text(msg) == "hello world"

    def test_assistant_msg_ignored(self) -> None:
        """Non-user messages are ignored."""
        from agentscope.message import AssistantMsg
        from xruntime._runtime._knowledge._middleware import (
            _extract_query_text,
        )

        msg = AssistantMsg(name="bot", content="hi")
        assert _extract_query_text(msg) == ""

    def test_resumption_event_ignored(self) -> None:
        """Resumption events yield empty string (no retrieval)."""
        from agentscope.event import UserConfirmResultEvent
        from xruntime._runtime._knowledge._middleware import (
            _extract_query_text,
        )

        evt = UserConfirmResultEvent(
            reply_id="r",
            confirm_results=[],
        )
        assert _extract_query_text(evt) == ""


class TestKnowledgeMiddleware:
    """Behavioral tests for KnowledgeMiddleware.on_reply."""

    async def test_static_control_injects_hint(self) -> None:
        """A hint is injected into context after ReplyStartEvent."""
        from agentscope.message import UserMsg, HintBlock
        from agentscope.event import ReplyStartEvent
        from xruntime._runtime._knowledge._middleware import (
            KnowledgeMiddleware,
        )

        registry = _FakeRegistry(_result_with_chunk())
        mw = KnowledgeMiddleware(
            registry=registry,
            mode="static_control",
        )
        agent = _FakeAgent()
        start_evt = ReplyStartEvent(
            session_id="s",
            reply_id="r",
            name="agent",
        )

        async def next_handler(**kwargs: Any):
            yield start_evt

        inputs = UserMsg(name="user", content="explain X")
        collected = [
            item
            async for item in mw.on_reply(
                agent=agent,
                input_kwargs={"inputs": inputs},
                next_handler=next_handler,
            )
        ]

        assert collected == [start_evt]
        assert len(agent.state.context) == 1
        injected = agent.state.context[0]
        assert injected.role == "assistant"
        assert isinstance(injected.content[0], HintBlock)
        assert "Relevant content" in injected.content[0].hint
        assert registry.last_query.query == "explain X"

    async def test_empty_result_no_injection(self) -> None:
        """An empty retrieval result injects nothing."""
        from agentscope.message import UserMsg
        from agentscope.event import ReplyStartEvent
        from xruntime._runtime._knowledge._middleware import (
            KnowledgeMiddleware,
        )

        registry = _FakeRegistry(KnowledgeResult(query="q"))
        mw = KnowledgeMiddleware(registry=registry)
        agent = _FakeAgent()

        async def next_handler(**kwargs: Any):
            yield ReplyStartEvent(
                session_id="s",
                reply_id="r",
                name="agent",
            )

        msg = UserMsg(name="user", content="hi")
        async for _ in mw.on_reply(
            agent=agent,
            input_kwargs={"inputs": msg},
            next_handler=next_handler,
        ):
            pass

        assert agent.state.context == []

    async def test_agent_control_is_passthrough(self) -> None:
        """agent_control mode injects nothing and passes inputs through."""
        from agentscope.message import UserMsg
        from agentscope.event import ReplyStartEvent
        from xruntime._runtime._knowledge._middleware import (
            KnowledgeMiddleware,
        )

        registry = _FakeRegistry(_result_with_chunk())
        mw = KnowledgeMiddleware(registry=registry, mode="agent_control")
        agent = _FakeAgent()
        received_kwargs: dict[str, Any] = {}

        async def next_handler(**kwargs: Any):
            received_kwargs.update(kwargs)
            yield ReplyStartEvent(
                session_id="s",
                reply_id="r",
                name="agent",
            )

        msg = UserMsg(name="user", content="hi")
        async for _ in mw.on_reply(
            agent=agent,
            input_kwargs={"inputs": msg},
            next_handler=next_handler,
        ):
            pass

        assert agent.state.context == []
        assert received_kwargs.get("inputs") is msg

    async def test_is_implemented_on_reply(self) -> None:
        """The AS middleware system must detect on_reply (not on_replying)."""
        from xruntime._runtime._knowledge._middleware import (
            KnowledgeMiddleware,
        )

        mw = KnowledgeMiddleware(
            registry=_FakeRegistry(KnowledgeResult(query="q"))
        )
        assert mw.is_implemented("on_reply") is True

    async def test_list_tools_static_control_empty(self) -> None:
        """static_control mode exposes no tools."""
        from xruntime._runtime._knowledge._middleware import (
            KnowledgeMiddleware,
        )

        mw = KnowledgeMiddleware(
            registry=_FakeRegistry(KnowledgeResult(query="q")),
            mode="static_control",
        )
        assert await mw.list_tools() == []

    async def test_list_tools_agent_control(self) -> None:
        """agent_control mode exposes search + ingest tools."""
        from xruntime._runtime._knowledge._middleware import (
            KnowledgeMiddleware,
        )
        from xruntime._runtime._knowledge._tools import (
            SearchKnowledgeTool,
            IngestKnowledgeTool,
        )

        mw = KnowledgeMiddleware(
            registry=_FakeRegistry(KnowledgeResult(query="q")),
            mode="agent_control",
        )
        tools = await mw.list_tools()
        assert len(tools) == 2
        assert isinstance(tools[0], SearchKnowledgeTool)
        assert isinstance(tools[1], IngestKnowledgeTool)


class TestRegistryDefaultFactory:
    """Tests that a registry built with the default factory resolves."""

    def test_register_from_config_with_default_factory(self) -> None:
        """A registry sharing the default factory resolves llm_wiki."""
        from xruntime._runtime._knowledge._adapter import (
            get_default_factory,
        )

        _register_default_adapter()
        registry = KnowledgeRegistry(factory=get_default_factory())
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = registry.register_from_config(
                KnowledgeBaseConfig(
                    backend="llm_wiki",
                    raw_dir=os.path.join(tmpdir, "raw"),
                    compiled_dir=os.path.join(tmpdir, "compiled"),
                ),
            )
            assert isinstance(adapter, LlmWikiAdapter)
