# -*- coding: utf-8 -*-
"""MemoryMiddleware — inject memories into system prompt and
extract new memories from conversation.

Uses :class:`MemoryStore` (sync API) for retrieval and storage.
Memories are injected via ``on_system_prompt`` (transformer pattern)
and extracted in the background after ``on_reply`` completes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase

from ._models import MemoryItem
from ._store import MemoryStore

logger = logging.getLogger("xruntime.middleware.memory")

_MEMORY_PROMPT_HEADER = (
    "\n\n## Long-term Memory\n"
    "The following may be relevant. If it conflicts with "
    "the current request, follow the current request.\n"
)


class MemoryMiddleware(MiddlewareBase):
    """Inject relevant memories into system prompt and extract
    new memories after each reply.

    Args:
        store (`MemoryStore`):
            The memory store (sync API).
        user_id (`str`):
            User identifier for memory retrieval.
        tenant_id (`str`):
            Tenant identifier for isolation.
        session_id (`str`):
            Session identifier for traceability.
        max_injected (`int`):
            Maximum number of memories to inject.
        confidence_threshold (`float`):
            Minimum confidence to include a memory.
    """

    def __init__(
        self,
        store: MemoryStore,
        user_id: str = "",
        tenant_id: str = "",
        session_id: str = "",
        max_injected: int = 5,
        confidence_threshold: float = 0.3,
        extractor: Any = None,
    ) -> None:
        """Initialize the middleware."""
        self._store = store
        self._user_id = user_id
        self._tenant_id = tenant_id
        self._session_id = session_id
        self._max_injected = max_injected
        self._confidence_threshold = confidence_threshold
        self._last_query: str = ""
        if extractor is not None:
            self._extractor = extractor
        else:
            from ._extractor import MockLLMExtractor

            self._extractor = MockLLMExtractor()

    async def on_system_prompt(
        self,
        agent: Any,
        current_prompt: str,
    ) -> str:
        """Inject relevant memories into the system prompt.

        Args:
            agent: The Agent instance.
            current_prompt: The current system prompt.

        Returns:
            The modified system prompt with memories appended.
        """
        if not self._user_id or not self._last_query:
            logger.debug(
                "memory injection skipped: user_id=%s query=%s",
                bool(self._user_id),
                bool(self._last_query),
            )
            return current_prompt

        memories = self._store.search(
            query=self._last_query,
            user_id=self._user_id,
            tenant_id=self._tenant_id,
            top_k=self._max_injected,
        )

        filtered = [
            m
            for m in memories
            if m.confidence >= self._confidence_threshold
            and not m.is_expired()
        ]

        if not filtered:
            logger.debug(
                "no matching memories for query '%s' (user=%s, tenant=%s)",
                self._last_query[:50],
                self._user_id,
                self._tenant_id,
            )
            return current_prompt

        logger.info(
            "injecting %d memories into system prompt (user=%s, query='%s')",
            len(filtered),
            self._user_id,
            self._last_query[:50],
        )

        lines = [_MEMORY_PROMPT_HEADER]
        for m in filtered:
            lines.append(
                f"- [{m.type}] {m.content} "
                f"(confidence: {m.confidence:.1f})"
            )

        return current_prompt + "\n".join(lines)

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Run the reply, then extract memories in the background.

        Captures the user's input for memory retrieval on the
        next ``on_system_prompt`` call, and queues background
        memory extraction after the reply completes.

        Args:
            agent: The Agent instance.
            input_kwargs: Input arguments.
            next_handler: Next handler in the chain.

        Yields:
            Events from the reply process.
        """
        # Capture user message for memory retrieval
        inputs = input_kwargs.get("inputs")
        if inputs is not None:
            if isinstance(inputs, str):
                self._last_query = inputs
            elif hasattr(inputs, "content"):
                content = inputs.content
                if isinstance(content, list):
                    self._last_query = " ".join(
                        str(t.get("text", ""))
                        for t in content
                        if isinstance(t, dict)
                    )
                elif isinstance(content, str):
                    self._last_query = content

        events: list[Any] = []
        async for event in next_handler():
            events.append(event)
            yield event

        # Background extraction — don't block
        if self._user_id:
            asyncio.create_task(
                self._extract_memories(events),
            )

    async def _extract_memories(
        self,
        events: list[Any],
    ) -> None:
        """Extract memorable facts from conversation events.

        Uses the injected extractor (LLM or heuristic fallback).

        Args:
            events: List of events from the reply.
        """
        try:
            items = await self._extractor.extract(
                events=events,
                user_id=self._user_id,
                tenant_id=self._tenant_id,
                session_id=self._session_id,
            )
            for item in items:
                self._store.add(item)
                logger.debug(
                    "Extracted %s: %s",
                    item.type,
                    item.content[:80],
                )
        except Exception:
            logger.debug(
                "Memory extraction failed (non-blocking)",
                exc_info=True,
            )
