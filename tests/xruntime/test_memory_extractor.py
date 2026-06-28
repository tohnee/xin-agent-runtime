# -*- coding: utf-8 -*-
"""Tests for LLMMemoryExtractor.

Tests use mock LLM by default. Real LLM tests are skipped
unless --run-e2e is passed.
"""
from __future__ import annotations

import pytest

from xruntime._runtime._memory._extractor import (
    ExtractionResult,
    LLMMemoryExtractor,
    MockLLMExtractor,
)
from xruntime._runtime._memory._models import MemoryItem


class FakeEvent:
    """Fake conversation event."""

    def __init__(self, content: str, role: str = "assistant") -> None:
        self.content = [{"type": "text", "text": content}]
        self.role = role


class TestMockLLMExtractor:
    """Mock extractor tests (no API needed)."""

    @pytest.mark.asyncio
    async def test_extract_preference(self) -> None:
        """Extracts preference from 'I prefer' text."""
        extractor = MockLLMExtractor()
        events = [
            FakeEvent("I prefer using Python for data analysis"),
        ]
        results = await extractor.extract(
            events=events,
            user_id="alice",
            tenant_id="acme",
            session_id="sess-1",
        )
        assert len(results) >= 1
        assert results[0].type == "preference"
        assert "Python" in results[0].content
        assert results[0].user_id == "alice"

    @pytest.mark.asyncio
    async def test_extract_fact(self) -> None:
        """Extracts fact from informative text."""
        extractor = MockLLMExtractor()
        events = [
            FakeEvent(
                "The project deadline is next Friday and the " "budget is $50k"
            ),
        ]
        results = await extractor.extract(
            events=events,
            user_id="alice",
            tenant_id="acme",
            session_id="sess-1",
        )
        assert len(results) >= 1
        assert results[0].type == "fact"
        assert "deadline" in results[0].content.lower()

    @pytest.mark.asyncio
    async def test_extract_no_memorable_content(self) -> None:
        """Returns empty list for non-memorable content."""
        extractor = MockLLMExtractor()
        events = [FakeEvent("OK, sounds good.")]
        results = await extractor.extract(
            events=events,
            user_id="alice",
            tenant_id="acme",
            session_id="sess-1",
        )
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_extract_multiple_types(self) -> None:
        """Extracts both preference and fact from mixed content."""
        extractor = MockLLMExtractor()
        events = [
            FakeEvent("I like using FastAPI for APIs"),
            FakeEvent("The server runs on port 8080"),
        ]
        results = await extractor.extract(
            events=events,
            user_id="alice",
            tenant_id="acme",
            session_id="sess-1",
        )
        types = {r.type for r in results}
        assert "preference" in types
        assert "fact" in types

    @pytest.mark.asyncio
    async def test_extract_empty_events(self) -> None:
        """Empty events returns empty list."""
        extractor = MockLLMExtractor()
        results = await extractor.extract(
            events=[],
            user_id="alice",
            tenant_id="acme",
            session_id="sess-1",
        )
        assert results == []


class TestLLMMemoryExtractorProtocol:
    """Verify LLMMemoryExtractor protocol compliance."""

    def test_has_extract_method(self) -> None:
        """Extractor has async extract method."""
        extractor = MockLLMExtractor()
        assert hasattr(extractor, "extract")
        import inspect

        assert inspect.iscoroutinefunction(extractor.extract)

    def test_extraction_result_model(self) -> None:
        """ExtractionResult is a valid model."""
        r = ExtractionResult(
            type="preference",
            content="User likes Python",
            confidence=0.85,
        )
        assert r.type == "preference"
        assert r.confidence == 0.85


class TestMiddlewareWithLLMExtraction:
    """Verify MemoryMiddleware can use LLM extractor."""

    @pytest.mark.asyncio
    async def test_middleware_uses_llm_extractor(self) -> None:
        """MemoryMiddleware extracts via LLM instead of heuristic."""
        from xruntime._runtime._memory._middleware import MemoryMiddleware
        from xruntime._runtime._memory._store import MemoryStore

        store = MemoryStore()
        extractor = MockLLMExtractor()
        mw = MemoryMiddleware(
            store=store,
            user_id="alice",
            tenant_id="acme",
            session_id="sess-1",
            extractor=extractor,
        )

        # Simulate reply events
        events = [
            FakeEvent("I prefer Python and FastAPI for web development"),
        ]

        await mw._extract_memories(events)

        # Should have extracted via LLM (not heuristic)
        all_memories = store.list_all(
            user_id="alice",
            tenant_id="acme",
        )
        assert len(all_memories) >= 1
        assert any("Python" in m.content for m in all_memories)
