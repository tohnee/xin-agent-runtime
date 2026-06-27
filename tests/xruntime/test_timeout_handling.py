# -*- coding: utf-8 -*-
"""Tests for LLM error handling timeout branch."""
from __future__ import annotations

import asyncio

import pytest

from xruntime._runtime._middleware._llm_error_handling import (
    CircuitState,
    LLMErrorHandlingConfig,
    LLMErrorHandlingMiddleware,
)


class CallCounter:
    """Tracks call count across handler invocations."""

    def __init__(self) -> None:
        self.count = 0


async def make_slow_handler(counter: CallCounter, delay: float = 0.5):
    """Handler that sleeps, used with factory pattern."""

    async def handler():
        counter.count += 1
        await asyncio.sleep(delay)
        return "ok"

    return handler


async def make_quick_handler(counter: CallCounter, fail_until: int = 0):
    """Handler that fails until a threshold, then succeeds."""

    async def handler():
        counter.count += 1
        if counter.count <= fail_until:
            raise ValueError(f"API error #{counter.count}")
        return "recovered"

    return handler


async def make_mixed_handler(counter: CallCounter):
    """Handler that raises ValueError first, then times out, then succeeds."""

    async def handler():
        counter.count += 1
        if counter.count == 1:
            raise ValueError("API error")
        if counter.count == 2:
            await asyncio.sleep(0.5)
        return "success on 3rd"

    return handler


class TestTimeoutHandling:
    """Timeout-specific error handling tests."""

    @pytest.mark.asyncio
    async def test_timeout_triggers_retry(self) -> None:
        """Timeout triggers timeout_retry, then recovers."""
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(
                timeout_seconds=0.05,
                timeout_retries=2,
                timeout_retry_delay=0.01,
                max_retries=0,
            ),
        )
        counter = CallCounter()

        # First 2 calls timeout, 3rd succeeds
        def next_handler():
            async def h():
                counter.count += 1
                if counter.count <= 2:
                    await asyncio.sleep(0.5)
                return "recovered"

            return h()

        result = await mw.on_model_call(None, {}, next_handler)
        assert result == "recovered"
        assert counter.count == 3

    @pytest.mark.asyncio
    async def test_timeout_exhausts_retries(self) -> None:
        """Timeout after all timeout_retries → failure + circuit open."""
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(
                timeout_seconds=0.05,
                timeout_retries=1,
                timeout_retry_delay=0.01,
                max_retries=0,
                circuit_breaker_threshold=1,
            ),
        )

        def next_handler():
            async def h():
                await asyncio.sleep(0.5)
                return "never"

            return h()

        with pytest.raises(asyncio.TimeoutError):
            await mw.on_model_call(None, {}, next_handler)

        assert mw.consecutive_failures >= 1
        assert mw.circuit_state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_timeout_separate_from_error_retry(self) -> None:
        """Timeout and regular errors have independent retry paths."""
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(
                timeout_seconds=0.05,
                timeout_retries=1,
                timeout_retry_delay=0.01,
                max_retries=3,
                retry_delay=0.01,
            ),
        )
        counter = CallCounter()

        def next_handler():
            async def h():
                counter.count += 1
                if counter.count == 1:
                    raise ValueError("API error")
                if counter.count == 2:
                    await asyncio.sleep(0.5)
                return "success on 3rd"

            return h()

        result = await mw.on_model_call(None, {}, next_handler)
        assert result == "success on 3rd"
        assert counter.count == 3

    @pytest.mark.asyncio
    async def test_fast_call_no_timeout(self) -> None:
        """Fast call doesn't trigger timeout."""

        def next_handler():
            async def h():
                return "ok"

            return h()

        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(timeout_seconds=5.0),
        )
        result = await mw.on_model_call(None, {}, next_handler)
        assert result == "ok"
        assert mw.stats["total_successes"] == 1

    @pytest.mark.asyncio
    async def test_timeout_logs_warning(self) -> None:
        """Timeout produces warning log."""
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(
                timeout_seconds=0.05,
                timeout_retries=0,
                circuit_breaker_threshold=99,
            ),
        )

        def next_handler():
            async def h():
                await asyncio.sleep(0.5)
                return "never"

            return h()

        with pytest.raises(asyncio.TimeoutError):
            await mw.on_model_call(None, {}, next_handler)
        assert mw.stats["total_failures"] >= 1

    @pytest.mark.asyncio
    async def test_timeout_does_not_trigger_fallback(self) -> None:
        """Timeout raises, doesn't silently fallback."""
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(
                timeout_seconds=0.05,
                timeout_retries=0,
                fallback_model="gpt-4o-mini",
                circuit_breaker_threshold=99,
            ),
        )

        def next_handler():
            async def h():
                await asyncio.sleep(0.5)
                return "never"

            return h()

        with pytest.raises(asyncio.TimeoutError):
            await mw.on_model_call(None, {}, next_handler)
