# -*- coding: utf-8 -*-
"""Tests for LangfuseTracerMiddleware exception handling.

Verifies that when ``next_handler`` raises, the tracer still records
a trace with ``success=False`` and re-raises the original exception
instead of swallowing it.
"""
from __future__ import annotations

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


async def _raising_async_gen(exc: BaseException):
    """Async generator that raises ``exc`` after yielding nothing."""
    raise exc
    yield  # pylint: disable=unreachable


class TestLangfuseTracerMiddlewareExceptions:
    """Exception-path tests for LangfuseTracerMiddleware."""

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
    async def test_on_acting_exception_records_success_false(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
    ) -> None:
        """on_acting records a tool trace with success=False when
        next_handler raises.
        """
        tc = FakeToolCall(name="bash")
        boom = RuntimeError("tool blew up")

        with pytest.raises(RuntimeError, match="tool blew up"):
            async for _ in mw.on_acting(
                FakeAgent(),
                {"tool_call": tc},
                lambda: _raising_async_gen(boom),
            ):
                pass

        # Trace was still emitted
        assert len(exporter.mock.tool_spans) == 1
        span = exporter.mock.tool_spans[0]
        assert "tool:bash" == span["name"]
        metadata = span["metadata"]
        assert metadata["success"] is False
        # duration is still recorded
        assert "duration_ms" in metadata

    @pytest.mark.asyncio
    async def test_on_acting_success_records_success_true(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
    ) -> None:
        """on_acting records a tool trace with success=True when
        next_handler completes normally.
        """
        tc = FakeToolCall(name="read_file")
        async for _ in mw.on_acting(
            FakeAgent(),
            {"tool_call": tc},
            lambda: _empty_async_gen(),
        ):
            pass

        assert len(exporter.mock.tool_spans) == 1
        span = exporter.mock.tool_spans[0]
        metadata = span["metadata"]
        assert metadata["success"] is True

    @pytest.mark.asyncio
    async def test_on_reasoning_exception_records_success_false(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
        agent: FakeAgent,
    ) -> None:
        """on_reasoning records a model trace with success=False when
        next_handler raises.
        """
        boom = RuntimeError("model blew up")

        with pytest.raises(RuntimeError, match="model blew up"):
            async for _ in mw.on_reasoning(
                agent,
                {},
                lambda: _raising_async_gen(boom),
            ):
                pass

        # Trace was still emitted
        assert len(exporter.mock.generations) == 1
        gen = exporter.mock.generations[0]
        metadata = gen["metadata"]
        assert metadata["success"] is False
        # duration is still recorded
        assert "duration_ms" in metadata

    @pytest.mark.asyncio
    async def test_on_reasoning_success_records_success_true(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
        agent: FakeAgent,
    ) -> None:
        """on_reasoning records a model trace with success=True when
        next_handler completes normally.
        """
        async for _ in mw.on_reasoning(
            agent,
            {},
            lambda: _empty_async_gen(),
        ):
            pass

        assert len(exporter.mock.generations) == 1
        gen = exporter.mock.generations[0]
        metadata = gen["metadata"]
        assert metadata["success"] is True

    @pytest.mark.asyncio
    async def test_on_acting_reraises_original_exception(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
    ) -> None:
        """on_acting must re-raise the original exception, not swallow it."""
        tc = FakeToolCall(name="bash")
        original = ValueError("original cause")

        with pytest.raises(ValueError, match="original cause"):
            async for _ in mw.on_acting(
                FakeAgent(),
                {"tool_call": tc},
                lambda: _raising_async_gen(original),
            ):
                pass

        # Exactly one trace recorded despite the raise
        assert len(exporter.mock.tool_spans) == 1

    @pytest.mark.asyncio
    async def test_on_reasoning_reraises_original_exception(
        self,
        mw: LangfuseTracerMiddleware,
        exporter: MockLangfuseExporter,
        agent: FakeAgent,
    ) -> None:
        """on_reasoning must re-raise the original exception."""
        original = KeyError("missing-key")

        with pytest.raises(KeyError, match="missing-key"):
            async for _ in mw.on_reasoning(
                agent,
                {},
                lambda: _raising_async_gen(original),
            ):
                pass

        # Exactly one trace recorded despite the raise
        assert len(exporter.mock.generations) == 1
