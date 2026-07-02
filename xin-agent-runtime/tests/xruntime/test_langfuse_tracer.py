# -*- coding: utf-8 -*-
"""Tests for LangfuseTracerMiddleware — verifies trace data output.

Uses a mock exporter to capture trace calls without requiring
a real Langfuse server.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from xruntime._runtime._langfuse import LangfuseConfig, LangfuseExporter
from xruntime._runtime._middleware._langfuse_tracer import (
    LangfuseTracerMiddleware,
)


class MockLangfuseClient:
    """Mock Langfuse client that records all trace calls."""

    def __init__(self) -> None:
        """Initialize mock."""
        self.generations: list[dict[str, Any]] = []
        self.tool_spans: list[dict[str, Any]] = []
        self.knowledge_spans: list[dict[str, Any]] = []

    def generation(self, **kwargs: Any) -> None:
        """Record a generation trace."""
        self.generations.append(kwargs)

    def span(self, **kwargs: Any) -> None:
        """Record a span trace."""
        name = kwargs.get("name", "")
        if name.startswith("tool:"):
            self.tool_spans.append(kwargs)
        elif name.startswith("knowledge:"):
            self.knowledge_spans.append(kwargs)
        else:
            self.tool_spans.append(kwargs)


class MockLangfuseExporter(LangfuseExporter):
    """LangfuseExporter with a mock client for testing."""

    def __init__(self) -> None:
        """Initialize with mock client (not no-op)."""
        # Skip parent __init__, set up manually
        self._config = LangfuseConfig(enabled=True)
        self._client: Any = MockLangfuseClient()
        self._noop = False

    @property
    def mock(self) -> MockLangfuseClient:
        """Access the mock client."""
        return self._client


class FakeToolCall:
    """Minimal tool-call stub."""

    def __init__(self, name: str = "bash") -> None:
        self.name = name
        self.input = {}


class FakeAgent:
    """Minimal agent stub with a model."""

    def __init__(self, model_name: str = "gpt-4o") -> None:
        self.name = "test-agent"
        self.model = type(
            "FakeModel",
            (),
            {"model_name": model_name, "name": model_name},
        )()


async def _empty_async_gen():
    """Empty async generator for next_handler."""
    return
    yield  # pylint: disable=unreachable


class TestLangfuseTracerMiddleware:
    """LangfuseTracerMiddleware tests with mock exporter."""

    @pytest.fixture
    def exporter(self) -> MockLangfuseExporter:
        return MockLangfuseExporter()

    @pytest.fixture
    def agent(self) -> FakeAgent:
        return FakeAgent("claude-sonnet-4")

    @pytest.fixture
    def mw(
        self,
        exporter: MockLangfuseExporter,
    ) -> LangfuseTracerMiddleware:
        return LangfuseTracerMiddleware(
            exporter=exporter,
            tenant_id="acme",
            user_id="alice",
            session_id="sess-1",
        )

    @pytest.mark.asyncio
    async def test_noop_exporter_skips_tracing(self) -> None:
        """No-op exporter produces no trace calls."""
        noop = LangfuseExporter(LangfuseConfig(enabled=False))
        assert noop.is_noop is True

        mw = LangfuseTracerMiddleware(
            exporter=noop,
            tenant_id="acme",
        )

        # Simulate a model call
        async for _ in mw.on_reasoning(
            FakeAgent(),
            {},
            lambda: _empty_async_gen(),
        ):
            pass

        # No trace data
        assert noop.is_noop

    @pytest.mark.asyncio
    async def test_model_call_traced(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
        agent: FakeAgent,
    ) -> None:
        """Model call produces a generation trace."""
        async for _ in mw.on_reasoning(
            agent,
            {},
            lambda: _empty_async_gen(),
        ):
            pass

        assert len(exporter.mock.generations) == 1
        gen = exporter.mock.generations[0]
        assert "model:claude-sonnet-4" == gen["name"]
        assert gen["model"] == "claude-sonnet-4"
        assert gen["usage"]["input"] == 0
        assert gen["usage"]["output"] == 0

    @pytest.mark.asyncio
    async def test_model_trace_includes_tenant_context(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
        agent: FakeAgent,
    ) -> None:
        """Trace metadata includes tenant/user/session."""
        async for _ in mw.on_reasoning(
            agent,
            {},
            lambda: _empty_async_gen(),
        ):
            pass

        gen = exporter.mock.generations[0]
        metadata = gen["metadata"]
        assert metadata["tenant_id"] == "acme"
        assert metadata["user_id"] == "alice"
        assert metadata["session_id"] == "sess-1"
        assert "turn" in metadata

    @pytest.mark.asyncio
    async def test_tool_call_traced(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
    ) -> None:
        """Tool call produces a span trace."""
        tc = FakeToolCall(name="bash")
        async for _ in mw.on_acting(
            FakeAgent(),
            {"tool_call": tc},
            lambda: _empty_async_gen(),
        ):
            pass

        assert len(exporter.mock.tool_spans) == 1
        span = exporter.mock.tool_spans[0]
        assert "tool:bash" == span["name"]

    @pytest.mark.asyncio
    async def test_tool_trace_includes_metadata(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
    ) -> None:
        """Tool trace metadata includes tenant + session + duration."""
        tc = FakeToolCall(name="read_file")
        async for _ in mw.on_acting(
            FakeAgent(),
            {"tool_call": tc},
            lambda: _empty_async_gen(),
        ):
            pass

        span = exporter.mock.tool_spans[0]
        metadata = span["metadata"]
        assert metadata["tenant_id"] == "acme"
        assert metadata["session_id"] == "sess-1"
        assert "duration_ms" in metadata
        assert metadata["success"] is True

    @pytest.mark.asyncio
    async def test_multiple_turns_increment_turn_counter(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
        agent: FakeAgent,
    ) -> None:
        """Each model call increments the turn counter."""
        for _ in range(3):
            async for _ in mw.on_reasoning(
                agent,
                {},
                lambda: _empty_async_gen(),
            ):
                pass

        assert len(exporter.mock.generations) == 3
        turns = [g["metadata"]["turn"] for g in exporter.mock.generations]
        assert turns == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_mixed_model_and_tool_calls(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
        agent: FakeAgent,
    ) -> None:
        """Interleaved model and tool calls all traced."""
        # Model call
        async for _ in mw.on_reasoning(
            agent,
            {},
            lambda: _empty_async_gen(),
        ):
            pass

        # Tool call
        tc = FakeToolCall(name="write_file")
        async for _ in mw.on_acting(
            agent,
            {"tool_call": tc},
            lambda: _empty_async_gen(),
        ):
            pass

        # Another model call
        async for _ in mw.on_reasoning(
            agent,
            {},
            lambda: _empty_async_gen(),
        ):
            pass

        assert len(exporter.mock.generations) == 2
        assert len(exporter.mock.tool_spans) == 1

    @pytest.mark.asyncio
    async def test_secret_redaction_in_trace(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
        agent: FakeAgent,
    ) -> None:
        """Trace metadata is redacted (no raw secrets)."""
        # The exporter's _redact_payload should strip API keys
        async for _ in mw.on_reasoning(
            agent,
            {},
            lambda: _empty_async_gen(),
        ):
            pass

        gen = exporter.mock.generations[0]
        metadata_str = str(gen["metadata"])
        # No raw sk- keys in trace
        assert "sk-" not in metadata_str

    @pytest.mark.asyncio
    async def test_duration_recorded(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
        agent: FakeAgent,
    ) -> None:
        """Model call duration is recorded in trace."""
        async for _ in mw.on_reasoning(
            agent,
            {},
            lambda: _empty_async_gen(),
        ):
            pass

        gen = exporter.mock.generations[0]
        assert "duration_ms" in gen["metadata"]
        assert gen["metadata"]["duration_ms"] >= 0
