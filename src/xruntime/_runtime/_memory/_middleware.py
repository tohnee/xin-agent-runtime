# -*- coding: utf-8 -*-
"""MemoryMiddleware — inject memories and extract new ones."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentscope.middleware import MiddlewareBase

from ._models import MemoryItem
from ._store import MemoryStore

logger = logging.getLogger(__name__)

_MEMORY_PROMPT_HEADER = (
    "\n\n## Long-term Memory\n"
    "The following may be relevant. If it conflicts with "
    "the current request, follow the current request.\n"
)


class MemoryMiddleware(MiddlewareBase):
    """Middleware that injects relevant memories and extracts new ones.

    Args:
        store (`MemoryStore`):
            The memory store to read/write.
        max_injected (`int`):
            Maximum number of memories to inject.
        confidence_threshold (`float`):
            Minimum confidence to include a memory.
    """

    def __init__(
        self,
        store: MemoryStore,
        max_injected: int = 5,
        confidence_threshold: float = 0.3,
    ) -> None:
        """Initialize the middleware."""
        self._store = store
        self._max_injected = max_injected
        self._confidence_threshold = confidence_threshold

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
        user_id = getattr(agent, "user_id", "")
        tenant_id = getattr(agent, "tenant_id", "")
        query = getattr(agent, "last_user_message", "")

        if not user_id or not query:
            return current_prompt

        memories = await self._store.search(
            query=query,
            user_id=user_id,
            tenant_id=tenant_id,
            top_k=self._max_injected,
        )

        filtered = [
            m
            for m in memories
            if m.confidence >= self._confidence_threshold
            and not m.is_expired()
        ]

        if not filtered:
            return current_prompt

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
        next_handler: Any,
    ) -> Any:
        """Run the reply, then extract memories in the background.

        Args:
            agent: The Agent instance.
            input_kwargs: Input arguments.
            next_handler: Next handler in the chain.

        Yields:
            Events from the reply process.
        """
        events = []
        async for event in next_handler():
            events.append(event)
            yield event

        # Background extraction — don't block
        user_id = getattr(agent, "user_id", "")
        tenant_id = getattr(agent, "tenant_id", "")
        session_id = getattr(agent, "session_id", "")
        if user_id:
            asyncio.create_task(
                self._extract_memories(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    events=events,
                ),
            )

    async def _extract_memories(
        self,
        user_id: str,
        tenant_id: str,
        session_id: str,
        events: list[Any],
    ) -> None:
        """Extract memorable facts from conversation events.

        This is a simple MVP implementation that looks for
        user preference signals in the conversation. V2 will
        use an LLM for extraction.

        Args:
            user_id: User identifier.
            tenant_id: Tenant identifier.
            session_id: Session identifier.
            events: List of events from the reply.
        """
        try:
            # MVP: extract from event text using simple heuristics
            for event in events:
                text = getattr(event, "content", "")
                if isinstance(text, list):
                    text = " ".join(
                        str(t.get("text", ""))
                        for t in text
                        if isinstance(t, dict)
                    )
                if not isinstance(text, str):
                    continue

                # Simple preference detection
                lower = text.lower()
                if "prefer" in lower or "i like" in lower:
                    item = MemoryItem(
                        user_id=user_id,
                        tenant_id=tenant_id,
                        type="preference",
                        content=text[:500],
                        source_session_id=session_id,
                        confidence=0.6,
                    )
                    await self._store.add(item)
                    logger.debug(
                        "Extracted preference memory: %s",
                        text[:80],
                    )
        except Exception:
            logger.debug(
                "Memory extraction failed (non-blocking)",
                exc_info=True,
            )
