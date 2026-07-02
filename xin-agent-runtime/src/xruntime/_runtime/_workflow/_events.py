# -*- coding: utf-8 -*-
"""P4-B: EventBus — event-driven workflow triggering.

Allows external systems to trigger workflows by publishing events.
Multiple subscribers can listen to the same event; handler
exceptions are caught and logged so one bad handler doesn't break
others.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger("xruntime.workflow.events")

Handler = Callable[[dict[str, Any]], Awaitable[None]]


class EventBus:
    """Async event bus for workflow triggers.

    Args:
        max_queue (`int`): Max queued events per subscriber.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = {}

    def subscribe(
        self,
        event_name: str,
        handler: Handler,
    ) -> None:
        """Subscribe a handler to an event.

        Args:
            event_name (`str`): The event name.
            handler (`Handler`): Async callable ``(data) -> None``.
        """
        if event_name not in self._subscribers:
            self._subscribers[event_name] = []
        self._subscribers[event_name].append(handler)

    def unsubscribe(
        self,
        event_name: str,
        handler: Handler,
    ) -> bool:
        """Remove a handler. Returns True if removed."""
        handlers = self._subscribers.get(event_name, [])
        if handler in handlers:
            handlers.remove(handler)
            return True
        return False

    async def publish(
        self,
        event_name: str,
        data: dict[str, Any] | None = None,
    ) -> int:
        """Publish an event to all subscribers.

        Args:
            event_name (`str`): The event name.
            data (`dict | None`): Event payload.

        Returns:
            `int`: Number of handlers invoked.
        """
        handlers = self._subscribers.get(event_name, [])
        if not handlers:
            return 0
        payload = data or {}
        tasks = [self._safe_call(h, event_name, payload) for h in handlers]
        await asyncio.gather(*tasks)
        return len(handlers)

    @property
    def subscriber_count(self) -> int:
        """Return total number of subscriptions."""
        return sum(len(hs) for hs in self._subscribers.values())

    async def _safe_call(
        self,
        handler: Handler,
        event_name: str,
        data: dict[str, Any],
    ) -> None:
        """Call handler, catching exceptions."""
        try:
            await handler(data)
        except Exception:  # noqa: BLE001
            logger.exception(
                "EventBus handler for '%s' raised",
                event_name,
            )


class EventTrigger:
    """Triggers a workflow when an event is published.

    Args:
        event_name (`str`): The event to listen for.
        workflow_id (`str`): The workflow to trigger.
        bus (`EventBus`): The event bus.
        on_trigger (`Callable | None`):
            Async callable invoked when the event fires.
    """

    def __init__(
        self,
        event_name: str,
        workflow_id: str,
        bus: EventBus,
        on_trigger: (
            Callable[[dict[str, Any]], Awaitable[None]] | None
        ) = None,
    ) -> None:
        self._event_name = event_name
        self._workflow_id = workflow_id
        self._bus = bus
        self._on_trigger = on_trigger
        self._fired_count = 0
        bus.subscribe(event_name, self._handle)

    async def _handle(self, data: dict[str, Any]) -> None:
        self._fired_count += 1
        if self._on_trigger is not None:
            await self._on_trigger(data)

    @property
    def fired_count(self) -> int:
        """Return how many times this trigger fired."""
        return self._fired_count

    @property
    def workflow_id(self) -> str:
        """Return the workflow id."""
        return self._workflow_id


__all__ = ["EventBus", "EventTrigger"]
