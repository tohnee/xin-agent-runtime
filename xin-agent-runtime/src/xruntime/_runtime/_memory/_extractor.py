# -*- coding: utf-8 -*-
"""LLMMemoryExtractor — extract memories from conversation using LLM.

Phase 3: Replaces heuristic extraction with LLM-powered extraction.

Protocol:
    extractor.extract(events, user_id, tenant_id, session_id)
    → list[MemoryItem]

Two implementations:
    - MockLLMExtractor: uses heuristic patterns (no API needed)
    - LLMMemoryExtractor: calls real LLM (requires API key)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ._models import MemoryItem

logger = logging.getLogger("xruntime.memory.extractor")


class ExtractionResult(BaseModel):
    """A single extraction result from the LLM.

    Args:
        type (`str`): Memory type — preference/fact/procedure/episode.
        content (`str`): Extracted memory content.
        confidence (`float`): Extraction confidence 0-1.
        tags (`list[str]`): Optional tags.
    """

    type: str = "fact"
    content: str
    confidence: float = 0.7
    tags: list[str] = Field(default_factory=list)


class MemoryExtractorProtocol(Protocol):
    """Protocol for memory extractors."""

    async def extract(
        self,
        events: list[Any],
        user_id: str,
        tenant_id: str,
        session_id: str,
    ) -> list[MemoryItem]:
        """Extract memories from conversation events.

        Args:
            events: List of conversation events.
            user_id: User identifier.
            tenant_id: Tenant identifier.
            session_id: Session identifier.

        Returns:
            `list[MemoryItem]`: Extracted memories.
        """
        ...


class MockLLMExtractor:
    """Mock LLM extractor using heuristic patterns.

    Simulates LLM extraction without requiring an API. Uses
    keyword detection to classify content as preference/fact.

    Used as default in MemoryMiddleware and for testing.
    """

    PREFERENCE_PATTERNS = [
        "prefer",
        "i like",
        "i love",
        "my favorite",
        "i use",
        "always use",
        "stick to",
    ]
    FACT_PATTERNS = [
        "deadline",
        "budget",
        "cost",
        "runs on",
        "located at",
        "consists of",
        "the server",
        "the project",
        "the api",
    ]

    async def extract(
        self,
        events: list[Any],
        user_id: str,
        tenant_id: str,
        session_id: str,
    ) -> list[MemoryItem]:
        """Extract memories using heuristic patterns.

        Args:
            events: List of conversation events.
            user_id: User identifier.
            tenant_id: Tenant identifier.
            session_id: Session identifier.

        Returns:
            `list[MemoryItem]`: Extracted memories.
        """
        results: list[MemoryItem] = []

        for event in events:
            text = self._get_text(event)
            if not text:
                continue

            lower = text.lower()

            # Check for preference
            for pattern in self.PREFERENCE_PATTERNS:
                if pattern in lower:
                    item = MemoryItem(
                        user_id=user_id,
                        tenant_id=tenant_id,
                        type="preference",
                        content=text[:500],
                        source_session_id=session_id,
                        confidence=0.75,
                        tags=self._extract_tags(text),
                    )
                    results.append(item)
                    logger.debug(
                        "Extracted preference: %s",
                        text[:80],
                    )
                    break

            # Check for fact (only if not already a preference)
            if not any(p in lower for p in self.PREFERENCE_PATTERNS):
                for pattern in self.FACT_PATTERNS:
                    if pattern in lower:
                        item = MemoryItem(
                            user_id=user_id,
                            tenant_id=tenant_id,
                            type="fact",
                            content=text[:500],
                            source_session_id=session_id,
                            confidence=0.65,
                            tags=self._extract_tags(text),
                        )
                        results.append(item)
                        logger.debug(
                            "Extracted fact: %s",
                            text[:80],
                        )
                        break

        return results

    @staticmethod
    def _get_text(event: Any) -> str:
        """Extract text from an event.

        Args:
            event: Conversation event.

        Returns:
            `str`: Text content.
        """
        content = getattr(event, "content", "")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text", "")))
                elif isinstance(part, str):
                    parts.append(part)
            return " ".join(parts)
        if isinstance(content, str):
            return content
        return ""

    @staticmethod
    def _extract_tags(text: str) -> list[str]:
        """Extract simple tags from text.

        Args:
            text: Input text.

        Returns:
            `list[str]`: Tags.
        """
        words = text.lower().split()
        tech_keywords = {
            "python",
            "rust",
            "fastapi",
            "django",
            "react",
            "vue",
            "docker",
            "redis",
            "postgres",
            "kubernetes",
            "api",
            "server",
            "database",
        }
        return [w for w in words if w in tech_keywords]


class LLMMemoryExtractor:
    """Real LLM-powered memory extractor.

    Calls an LLM to analyze conversation events and extract
    structured memories. Falls back to MockLLMExtractor when
    no model is available.

    Args:
        model (`Any | None`):
            A configured chat model (OpenAIChatModel, etc.).
            If None, falls back to heuristic extraction.
        max_content_length (`int`):
            Maximum characters of conversation to send to LLM.
    """

    EXTRACTION_PROMPT = (
        "Analyze the following conversation and extract memorable "
        "information. For each item, classify it as:\n"
        "- preference: User preferences or habits\n"
        "- fact: Important factual information\n"
        "- procedure: How-to or step-by-step knowledge\n"
        "- episode: Notable events or decisions\n\n"
        'Return JSON array: [{"type": "...", "content": "...", '
        '"confidence": 0.0-1.0, "tags": [...]}]\n'
        "If nothing memorable, return [].\n\n"
        "Conversation:\n"
    )

    def __init__(
        self,
        model: Any | None = None,
        max_content_length: int = 2000,
    ) -> None:
        """Initialize the extractor."""
        self._model = model
        self._max_length = max_content_length
        self._fallback = MockLLMExtractor()

    async def extract(
        self,
        events: list[Any],
        user_id: str,
        tenant_id: str,
        session_id: str,
    ) -> list[MemoryItem]:
        """Extract memories using LLM.

        Args:
            events: List of conversation events.
            user_id: User identifier.
            tenant_id: Tenant identifier.
            session_id: Session identifier.

        Returns:
            `list[MemoryItem]`: Extracted memories.
        """
        if self._model is None:
            return await self._fallback.extract(
                events,
                user_id,
                tenant_id,
                session_id,
            )

        # Build conversation text
        conv_parts: list[str] = []
        for event in events:
            text = MockLLMExtractor._get_text(event)
            if text:
                conv_parts.append(text)
        conv_text = "\n".join(conv_parts)[: self._max_length]

        if not conv_text.strip():
            return []

        try:
            results = await self._call_llm(conv_text)
            return [
                MemoryItem(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    type=r.type,
                    content=r.content,
                    source_session_id=session_id,
                    confidence=r.confidence,
                    tags=r.tags,
                )
                for r in results
            ]
        except Exception:
            logger.warning(
                "LLM extraction failed, falling back to heuristic",
                exc_info=True,
            )
            return await self._fallback.extract(
                events,
                user_id,
                tenant_id,
                session_id,
            )

    async def _call_llm(
        self,
        conversation_text: str,
    ) -> list[ExtractionResult]:
        """Call LLM to extract memories.

        Args:
            conversation_text: Conversation content.

        Returns:
            `list[ExtractionResult]`: Parsed results.
        """
        from agentscope.message import Msg

        messages = [
            Msg(
                role="system",
                name="system",
                content=[
                    {
                        "type": "text",
                        "text": (
                            "You are a memory extraction assistant. "
                            "Extract memorable information from "
                            "conversations. Return only JSON."
                        ),
                    },
                ],
            ),
            Msg(
                role="user",
                name="user",
                content=[
                    {
                        "type": "text",
                        "text": self.EXTRACTION_PROMPT + conversation_text,
                    },
                ],
            ),
        ]

        response = await self._model(messages)
        text = str(response)

        # Parse JSON from response
        try:
            # Find JSON array in response
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1:
                json_str = text[start : end + 1]
                items = json.loads(json_str)
                return [ExtractionResult(**item) for item in items]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        return []
