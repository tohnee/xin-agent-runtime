# -*- coding: utf-8 -*-
"""Quota middleware — enforces tenant-level usage limits.

Tracks token consumption, tool call count, and cost (USD) per
session.  Raises :class:`QuotaExceededError` when any limit is
breached.

Inherits :class:`agentscope.middleware.MiddlewareBase`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase


class QuotaExceededError(Exception):
    """Raised when a quota limit is exceeded.

    Attributes:
        limit_type (`str`):
            Which limit was exceeded.
        current (`float`):
            Current usage.
        limit (`float`):
            The configured limit.
    """

    def __init__(
        self,
        limit_type: str,
        current: float,
        limit: float,
    ) -> None:
        """Initialize the error."""
        self.limit_type = limit_type
        self.current = current
        self.limit = limit
        super().__init__(
            f"Quota exceeded: {limit_type} " f"({current}/{limit})",
        )


@dataclass
class QuotaConfig:
    """Quota limits for a single session or tenant.

    Args:
        max_tokens (`int | None`):
            Maximum total tokens. ``None`` = unlimited.
        max_tool_calls (`int | None`):
            Maximum tool call count per reply.
        max_cost_usd (`float | None`):
            Maximum total cost in USD.
    """

    max_tokens: int | None = None
    max_tool_calls: int | None = None
    max_cost_usd: float | None = None


class QuotaTracker:
    """Tracks usage against a :class:`QuotaConfig`.

    Args:
        config (`QuotaConfig`):
            The quota limits.
    """

    def __init__(self, config: QuotaConfig) -> None:
        """Initialize the tracker."""
        self.config = config
        self.token_usage: int = 0
        self.tool_call_count: int = 0
        self.cost_usd: float = 0.0

    def consume_tokens(self, amount: int) -> None:
        """Consume tokens, raising if over limit.

        Args:
            amount (`int`):
                Number of tokens to consume.
        """
        self.token_usage += amount
        if (
            self.config.max_tokens is not None
            and self.token_usage > self.config.max_tokens
        ):
            raise QuotaExceededError(
                "tokens",
                self.token_usage,
                self.config.max_tokens,
            )

    def consume_tool_call(self) -> None:
        """Increment tool call count, raising if over limit."""
        self.tool_call_count += 1
        if (
            self.config.max_tool_calls is not None
            and self.tool_call_count > self.config.max_tool_calls
        ):
            raise QuotaExceededError(
                "tool_calls",
                self.tool_call_count,
                self.config.max_tool_calls,
            )

    def consume_cost(self, amount: float) -> None:
        """Consume cost in USD, raising if over limit.

        Args:
            amount (`float`):
                Cost in USD to consume.
        """
        self.cost_usd += amount
        if (
            self.config.max_cost_usd is not None
            and self.cost_usd > self.config.max_cost_usd
        ):
            raise QuotaExceededError(
                "cost_usd",
                self.cost_usd,
                self.config.max_cost_usd,
            )


class QuotaMiddleware(MiddlewareBase):
    """Middleware that enforces quota limits on tool calls.

    Args:
        config (`QuotaConfig`):
            The quota limits to enforce.
        tracker (`QuotaTracker | None`):
            An externally-managed tracker to share quota state
            across turns.  When ``None`` (default), a fresh
            :class:`QuotaTracker` is created — which means the
            quota resets every turn.  Pass a shared tracker to
            accumulate usage across turns within a session.
    """

    def __init__(
        self,
        config: QuotaConfig,
        tracker: QuotaTracker | None = None,
    ) -> None:
        """Initialize the middleware."""
        self.tracker = tracker or QuotaTracker(config)

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Check tool call quota, then delegate.

        Args:
            agent (`Agent`):
                The agent instance.
            input_kwargs (`dict`):
                Contains ``tool_call``.
            next_handler (`Callable`):
                The next middleware or ``_acting_impl``.

        Yields:
            Tool chunks from the tool execution.

        Raises:
            QuotaExceededError: If the tool call quota is exceeded.
        """
        self.tracker.consume_tool_call()
        async for chunk in next_handler():
            yield chunk

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., Any],
    ) -> Any:
        """Track token (and cost) usage from each model API call.

        Wraps the raw model call so that token usage is metered against
        the session quota. The model call may return either a single
        :class:`ChatResponse` (non-streaming) or an async generator of
        ``ChatResponse`` chunks (streaming). For streaming responses the
        usage is read from the final chunk so tokens are counted once.

        Args:
            agent (`Agent`):
                The agent instance.
            input_kwargs (`dict`):
                Model-call kwargs (``messages``, ``tools``,
                ``tool_choice``, ``current_model``).
            next_handler (`Callable`):
                The next middleware or the raw model call.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                The (possibly streamed) model response.

        Raises:
            QuotaExceededError: If the token or cost quota is exceeded.
        """
        import inspect

        result = await next_handler(**input_kwargs)

        if inspect.isasyncgen(result):

            async def _metered() -> AsyncGenerator[Any, None]:
                last: Any = None
                async for chunk in result:
                    last = chunk
                    yield chunk
                if last is not None:
                    self._consume_usage(last)

            return _metered()

        self._consume_usage(result)
        return result

    def _consume_usage(self, response: Any) -> None:
        """Meter token / cost usage from a single model response.

        Args:
            response (`ChatResponse`):
                The completed model response (or final stream chunk).

        Raises:
            QuotaExceededError: If a limit is exceeded.
        """
        usage = getattr(response, "usage", None)
        if usage is not None:
            tokens = getattr(usage, "input_tokens", 0) + getattr(
                usage,
                "output_tokens",
                0,
            )
            if tokens:
                self.tracker.consume_tokens(tokens)

        # Cost is optional — only present when a model/provider attaches
        # a ``cost`` (USD) to the response metadata.
        metadata = getattr(response, "metadata", None)
        if isinstance(metadata, dict):
            cost = metadata.get("cost")
            if isinstance(cost, (int, float)) and cost > 0:
                self.tracker.consume_cost(float(cost))
