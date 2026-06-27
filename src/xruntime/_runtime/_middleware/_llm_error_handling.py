# -*- coding: utf-8 -*-
"""LLM error handling middleware — retry, fallback, circuit breaker.

Wraps model calls with exponential-backoff retry, optional model
fallback, and a circuit breaker to prevent cascading failures.
"""
from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncGenerator, Awaitable, Callable

from agentscope.middleware import MiddlewareBase

if TYPE_CHECKING:
    from agentscope.agent import Agent
    from agentscope.model import ChatResponse


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker is open."""

    def __init__(self, reset_in: float) -> None:
        """Initialize error."""
        self.reset_in = reset_in
        super().__init__(
            f"Circuit breaker is OPEN. " f"Retry in {reset_in:.0f}s.",
        )


class LLMErrorHandlingConfig:
    """Configuration for LLM error handling.

    Args:
        max_retries (`int`):
            Maximum retry attempts on transient errors.
        retry_delay (`float`):
            Initial delay before first retry (seconds).
        retry_backoff (`float`):
            Backoff multiplier between retries.
        max_delay (`float`):
            Maximum delay between retries (seconds).
        fallback_model (`str`):
            Model name to switch to on persistent failure.
            Empty string disables fallback.
        circuit_breaker_threshold (`int`):
            Consecutive failures before opening the breaker.
        circuit_breaker_reset_time (`float`):
            Seconds before the breaker transitions OPEN → HALF_OPEN.
    """

    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        retry_backoff: float = 2.0,
        max_delay: float = 30.0,
        fallback_model: str = "",
        circuit_breaker_threshold: int = 5,
        circuit_breaker_reset_time: float = 60.0,
    ) -> None:
        """Initialize config."""
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.retry_backoff = retry_backoff
        self.max_delay = max_delay
        self.fallback_model = fallback_model
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_reset_time = circuit_breaker_reset_time


class LLMErrorHandlingMiddleware(MiddlewareBase):
    """Middleware that handles LLM call failures gracefully.

    Provides three layers of protection:

    1. **Retry** — transient errors (timeouts, rate limits) are
       retried with exponential backoff.
    2. **Fallback** — after exhausting retries, optionally switches
       to a fallback model.
    3. **Circuit breaker** — after ``circuit_breaker_threshold``
       consecutive failures, blocks all model calls for
       ``circuit_breaker_reset_time`` seconds.
    """

    def __init__(
        self,
        config: LLMErrorHandlingConfig | None = None,
    ) -> None:
        """Initialize the middleware.

        Args:
            config: Configuration object.
        """
        self._config = config or LLMErrorHandlingConfig()
        self._consecutive_failures: int = 0
        self._circuit_state: CircuitState = CircuitState.CLOSED
        self._circuit_opened_at: float = 0.0
        self._total_retries: int = 0
        self._total_failures: int = 0
        self._total_successes: int = 0

    async def on_model_call(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[
            ...,
            Awaitable["ChatResponse" | AsyncGenerator["ChatResponse", None]],
        ],
    ) -> "ChatResponse" | AsyncGenerator["ChatResponse", None]:
        """Intercept model calls with retry and circuit breaker.

        Args:
            agent: The Agent instance.
            input_kwargs: Dict with ``messages``, ``tools``, etc.
            next_handler: Next middleware or original model call.

        Returns:
            ChatResponse or async generator of ChatResponse.

        Raises:
            CircuitBreakerOpenError: When the breaker is OPEN.
        """
        self._check_circuit()

        last_error: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                result = await next_handler()
                self._on_success()
                return result
            except Exception as exc:
                last_error = exc
                self._total_failures += 1
                if attempt < self._config.max_retries:
                    delay = self._compute_delay(attempt)
                    self._total_retries += 1
                    await asyncio.sleep(delay)
                else:
                    self._on_failure()

        if last_error:
            raise last_error

    def _check_circuit(self) -> None:
        """Check and update circuit breaker state."""
        if self._circuit_state == CircuitState.OPEN:
            elapsed = time.time() - self._circuit_opened_at
            if elapsed >= self._config.circuit_breaker_reset_time:
                self._circuit_state = CircuitState.HALF_OPEN
            else:
                raise CircuitBreakerOpenError(
                    self._config.circuit_breaker_reset_time - elapsed
                )

    def _on_success(self) -> None:
        """Record a successful call."""
        self._consecutive_failures = 0
        self._total_successes += 1
        if self._circuit_state == CircuitState.HALF_OPEN:
            self._circuit_state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        """Record a failed call (internal)."""
        self._consecutive_failures += 1
        if (
            self._consecutive_failures
            >= self._config.circuit_breaker_threshold
        ):
            self._circuit_state = CircuitState.OPEN
            self._circuit_opened_at = time.time()

    # -- Public test-friendly API --

    def record_failure(self) -> None:
        """Record a failure and possibly open the breaker."""
        self._on_failure()

    def record_success(self) -> None:
        """Record a success and possibly close the breaker."""
        self._on_success()

    def check_circuit(self) -> None:
        """Check/update circuit state. Raises if OPEN."""
        self._check_circuit()

    def should_retry(self, attempt: int) -> bool:
        """Whether a retry should be attempted.

        Args:
            attempt: Zero-based attempt index.

        Returns:
            True if ``attempt < max_retries``.
        """
        return attempt < self._config.max_retries

    def handle_error(
        self,
        error: Exception,
        attempt: int,
    ) -> dict[str, Any] | None:
        """Handle an error, returning a decision dict or None.

        Args:
            error: The exception that occurred.
            attempt: Zero-based attempt index.

        Returns:
            Dict with ``fallback_model`` if fallback is configured,
            otherwise None.
        """
        self._on_failure()
        if self._config.fallback_model:
            return {"fallback_model": self._config.fallback_model}
        return None

    @property
    def consecutive_failures(self) -> int:
        """Current consecutive failure count."""
        return self._consecutive_failures

    def _compute_delay(self, attempt: int) -> float:
        """Compute exponential backoff delay.

        Args:
            attempt: Zero-based attempt index.

        Returns:
            Delay in seconds, capped at ``max_delay``.
        """
        delay = self._config.retry_delay * (
            self._config.retry_backoff**attempt
        )
        return min(delay, self._config.max_delay)

    def reset(self) -> None:
        """Reset all counters and circuit state.

        Call this at the start of a new session.
        """
        self._consecutive_failures = 0
        self._circuit_state = CircuitState.CLOSED
        self._circuit_opened_at = 0.0

    @property
    def circuit_state(self) -> CircuitState:
        """Current circuit breaker state."""
        return self._circuit_state

    @property
    def stats(self) -> dict[str, Any]:
        """Runtime statistics."""
        return {
            "consecutive_failures": self._consecutive_failures,
            "circuit_state": self._circuit_state.value,
            "total_retries": self._total_retries,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
        }
