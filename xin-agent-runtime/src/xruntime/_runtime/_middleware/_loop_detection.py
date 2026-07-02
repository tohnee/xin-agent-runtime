# -*- coding: utf-8 -*-
"""Loop detection middleware — prevents Agent from repeating actions.

Tracks recent tool calls (tool name + argument hash) within a sliding
window.  When the same call repeats more than ``max_repeats`` times,
the middleware injects a break-out message prompting the Agent to
try a different approach.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase

if TYPE_CHECKING:
    from agentscope.agent import Agent

logger = logging.getLogger("xruntime.middleware.loop_detection")


class LoopDetectionConfig:
    """Configuration for loop detection.

    Args:
        max_repeats (`int`):
            Maximum allowed repetitions of the same
            (tool_name, args_hash) pair before blocking.
        window_size (`int`):
            Number of recent tool calls to consider.
        block_message (`str`):
            Message injected into the Agent context when
            a loop is detected.
    """

    def __init__(
        self,
        max_repeats: int = 3,
        window_size: int = 10,
        block_message: str = (
            "You seem to be repeating the same action. "
            "Try a different approach or ask the user "
            "for clarification."
        ),
    ) -> None:
        """Initialize config."""
        self.max_repeats = max_repeats
        self.window_size = window_size
        self.block_message = block_message


class LoopDetectionMiddleware(MiddlewareBase):
    """Middleware that detects and breaks repetitive tool-call loops.

    Maintains a bounded deque of ``(tool_name, args_hash)`` tuples.
    On each ``on_acting`` invocation, counts how many times the
    *current* call signature appears in the window.  If the count
    exceeds ``max_repeats``, the tool call is allowed to proceed but
    a warning message is yielded before the result, nudging the
    Agent to change strategy.

    The middleware is stateful per-session; call :meth:`reset` when
    starting a new session or turn.
    """

    def __init__(
        self,
        config: LoopDetectionConfig | None = None,
    ) -> None:
        """Initialize the middleware.

        Args:
            config (`LoopDetectionConfig | None`):
                Configuration.  Defaults to ``max_repeats=3``,
                ``window_size=10``.
        """
        self._config = config or LoopDetectionConfig()
        self._history: deque[tuple[str, str]] = deque(
            maxlen=self._config.window_size,
        )

    async def on_acting(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Intercept tool execution to detect loops.

        Args:
            agent: The Agent instance.
            input_kwargs: Dict with ``tool_call`` key.
            next_handler: Next middleware or original impl.

        Yields:
            Events from the tool execution, preceded by a
            warning event if a loop is detected.
        """
        tool_call = input_kwargs.get("tool_call")
        tool_name = getattr(tool_call, "name", "unknown")
        args_hash = self._hash_args(
            getattr(tool_call, "input", None)
            or getattr(tool_call, "arguments", None)
            or {},
        )
        current = (tool_name, args_hash)

        self._history.append(current)

        repeat_count = sum(1 for item in self._history if item == current)

        if repeat_count > 1:
            logger.debug(
                "tool=%s repeat_count=%d/%d window=%d",
                tool_name,
                repeat_count,
                self._config.max_repeats,
                len(self._history),
            )

        looped = repeat_count > self._config.max_repeats

        if looped:
            logger.warning(
                "LOOP DETECTED: tool=%s repeated %d times "
                "(max=%d). Injecting break message.",
                tool_name,
                repeat_count,
                self._config.max_repeats,
            )
            from agentscope.message import Msg

            yield Msg(
                name="system",
                role="system",
                content=[{"type": "text", "text": self._config.block_message}],
            )

        async for event in next_handler():
            yield event

    @staticmethod
    def _hash_args(args: Any) -> str:
        """Generate a stable hash for tool arguments.

        Args:
            args: Tool call arguments (dict or any serialisable).

        Returns:
            MD5 hex digest of the JSON-serialised arguments.
        """
        try:
            payload = json.dumps(
                args,
                sort_keys=True,
                default=str,
            )
        except (TypeError, ValueError):
            payload = str(args)
        return hashlib.md5(payload.encode()).hexdigest()

    def reset(self) -> None:
        """Clear call history.

        Call this at the start of a new session or when the
        Agent's context is compressed.
        """
        self._history.clear()

    @property
    def history(self) -> list[tuple[str, str]]:
        """Read-only view of the current call history."""
        return list(self._history)
