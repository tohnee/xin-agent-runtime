# -*- coding: utf-8 -*-
"""Tests for ``_timeout_count`` reset in ``LLMErrorHandlingMiddleware``.

Verifies that the timeout retry counter is:
1. Initialized in ``__init__`` (not lazily created).
2. Reset at the start of each ``on_model_call`` invocation.
3. Reset by the ``reset()`` method.

Without these fixes, ``_timeout_count`` accumulates monotonically
across calls: once the budget is exhausted in the first call, all
subsequent calls lose timeout retry capability entirely.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from xruntime._runtime._middleware._llm_error_handling import (
    LLMErrorHandlingConfig,
    LLMErrorHandlingMiddleware,
)


def _config(timeout_retries: int = 2) -> LLMErrorHandlingConfig:
    return LLMErrorHandlingConfig(
        timeout_seconds=0.1,
        timeout_retries=timeout_retries,
        timeout_retry_delay=0.01,
        max_retries=0,
        retry_delay=0.01,
        retry_backoff=1.0,
        max_delay=0.05,
        circuit_breaker_threshold=99,
        circuit_breaker_reset_time=1.0,
    )


class TestTimeoutCountInit:
    """D1-1: ``_timeout_count`` must be declared in ``__init__``."""

    def test_timeout_count_initialized_to_zero(self) -> None:
        """``__init__`` should set ``_timeout_count = 0``."""
        mw = LLMErrorHandlingMiddleware(_config())
        assert hasattr(
            mw, "_timeout_count"
        ), "_timeout_count must be declared in __init__"
        assert mw._timeout_count == 0


class TestTimeoutCountPerCallReset:
    """D1-2: ``_timeout_count`` must reset per ``on_model_call``."""

    async def test_second_call_gets_full_timeout_retry_budget(
        self,
    ) -> None:
        """After exhausting timeout retries in call 1, call 2 must
        still get the full ``timeout_retries`` budget."""
        mw = LLMErrorHandlingMiddleware(_config(timeout_retries=2))

        call_count = 0

        async def always_timeout() -> Any:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(1.0)  # trigger TimeoutError
            return "never"

        # Call 1: exhausts timeout retries (2 retries + 1 = 3 attempts)
        with pytest.raises(asyncio.TimeoutError):
            await mw.on_model_call(
                agent=None,
                input_kwargs={},
                next_handler=always_timeout,
            )
        first_call_count = call_count
        assert (
            first_call_count == 3
        ), f"call 1 should have tried 3 times, got {first_call_count}"

        # Call 2: must still try 3 times, not 1
        call_count = 0
        with pytest.raises(asyncio.TimeoutError):
            await mw.on_model_call(
                agent=None,
                input_kwargs={},
                next_handler=always_timeout,
            )
        second_call_count = call_count
        assert second_call_count == 3, (
            f"call 2 should have tried 3 times (timeout_count reset), "
            f"got {second_call_count} — _timeout_count was not reset"
        )

    async def test_successful_call_resets_timeout_count(self) -> None:
        """A successful call between two failing calls must reset
        the timeout counter."""
        mw = LLMErrorHandlingMiddleware(_config(timeout_retries=2))

        attempt_counts: list[int] = []

        async def fail_then_succeed_then_fail() -> Any:
            attempt_counts.append(len(attempt_counts) + 1)
            if len(attempt_counts) <= 3:
                await asyncio.sleep(1.0)  # fail first 3
            elif len(attempt_counts) == 4:
                return "ok"  # succeed on 4th
            else:
                await asyncio.sleep(1.0)  # fail again

        # Call 1: 3 timeouts
        with pytest.raises(asyncio.TimeoutError):
            await mw.on_model_call(
                agent=None,
                input_kwargs={},
                next_handler=fail_then_succeed_then_fail,
            )
        assert len(attempt_counts) == 3

        # Call 2: succeeds immediately
        attempt_counts.clear()

        async def succeed() -> Any:
            return "ok"

        result = await mw.on_model_call(
            agent=None,
            input_kwargs={},
            next_handler=succeed,
        )
        assert result == "ok"

        # Call 3: must still get full 3 timeout retries
        attempt_counts.clear()
        with pytest.raises(asyncio.TimeoutError):
            await mw.on_model_call(
                agent=None,
                input_kwargs={},
                next_handler=fail_then_succeed_then_fail,
            )
        assert len(attempt_counts) == 3, (
            f"call 3 should have tried 3 times, got "
            f"{len(attempt_counts)} — _timeout_count not reset after "
            f"successful call"
        )


class TestTimeoutCountResetMethod:
    """D1-3: ``reset()`` must clear ``_timeout_count``."""

    def test_reset_clears_timeout_count(self) -> None:
        """``reset()`` should set ``_timeout_count`` back to 0."""
        mw = LLMErrorHandlingMiddleware(_config())
        # Simulate accumulated timeouts
        mw._timeout_count = 5
        mw.reset()
        assert mw._timeout_count == 0, "reset() must clear _timeout_count"

    def test_reset_clears_all_counters(self) -> None:
        """``reset()`` should clear all counters including
        ``_timeout_count``."""
        mw = LLMErrorHandlingMiddleware(_config())
        mw._timeout_count = 3
        mw._consecutive_failures = 5
        mw.reset()
        assert mw._timeout_count == 0
        assert mw._consecutive_failures == 0


class TestStatsIncludeTimeoutCount:
    """D1-4: ``stats`` property should include ``timeout_count``."""

    def test_stats_has_timeout_count(self) -> None:
        """``stats`` dict must include ``timeout_count``."""
        mw = LLMErrorHandlingMiddleware(_config())
        stats = mw.stats
        assert (
            "timeout_count" in stats
        ), "stats must include 'timeout_count' for observability"
        assert stats["timeout_count"] == 0
