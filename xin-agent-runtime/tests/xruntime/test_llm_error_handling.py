# -*- coding: utf-8 -*-
"""Tests for LLMErrorHandlingMiddleware."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xruntime._runtime._middleware._llm_error_handling import (
    CircuitBreakerOpenError,
    CircuitState,
    LLMErrorHandlingConfig,
    LLMErrorHandlingMiddleware,
)


class TestLLMErrorHandlingConfig:
    """Config defaults and customisation."""

    def test_defaults(self) -> None:
        cfg = LLMErrorHandlingConfig()
        assert cfg.max_retries == 3
        assert cfg.retry_delay == 1.0
        assert cfg.retry_backoff == 2.0
        assert cfg.max_delay == 30.0
        assert cfg.fallback_model == ""
        assert cfg.circuit_breaker_threshold == 5
        assert cfg.circuit_breaker_reset_time == 60.0

    def test_custom(self) -> None:
        cfg = LLMErrorHandlingConfig(
            max_retries=5,
            retry_delay=0.5,
            fallback_model="gpt-4o-mini",
            circuit_breaker_threshold=3,
        )
        assert cfg.max_retries == 5
        assert cfg.retry_delay == 0.5
        assert cfg.fallback_model == "gpt-4o-mini"
        assert cfg.circuit_breaker_threshold == 3


class TestCircuitBreaker:
    """Circuit breaker state transitions."""

    def test_initial_state_closed(
        self,
    ) -> None:
        mw = LLMErrorHandlingMiddleware()
        assert mw.circuit_state == CircuitState.CLOSED
        assert mw.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_failures_increment(self) -> None:
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(circuit_breaker_threshold=3),
        )
        mw.record_failure()
        assert mw.consecutive_failures == 1
        mw.record_failure()
        assert mw.consecutive_failures == 2
        assert mw.circuit_state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_opens_on_threshold(self) -> None:
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(circuit_breaker_threshold=3),
        )
        for _ in range(3):
            mw.record_failure()
        assert mw.circuit_state == CircuitState.OPEN
        assert mw.consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_circuit_blocks_when_open(self) -> None:
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(circuit_breaker_threshold=1),
        )
        mw.record_failure()  # Opens circuit
        assert mw.circuit_state == CircuitState.OPEN

        with pytest.raises(CircuitBreakerOpenError):
            mw.check_circuit()

    @pytest.mark.asyncio
    async def test_circuit_half_open_after_timeout(self) -> None:
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(
                circuit_breaker_threshold=1,
                circuit_breaker_reset_time=0.1,
            ),
        )
        mw.record_failure()
        assert mw.circuit_state == CircuitState.OPEN

        # Wait for reset time
        time.sleep(0.15)
        mw.check_circuit()
        assert mw.circuit_state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_circuit_closes_on_success(self) -> None:
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(circuit_breaker_threshold=1),
        )
        mw.record_failure()
        mw._circuit_state = CircuitState.HALF_OPEN

        mw.record_success()
        assert mw.circuit_state == CircuitState.CLOSED
        assert mw.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_success_resets_failures(self) -> None:
        mw = LLMErrorHandlingMiddleware()
        mw.record_failure()
        mw.record_failure()
        assert mw.consecutive_failures == 2

        mw.record_success()
        assert mw.consecutive_failures == 0


class TestRetryDelay:
    """Retry delay calculation with exponential backoff."""

    def test_delay_increases_exponentially(self) -> None:
        cfg = LLMErrorHandlingConfig(
            retry_delay=1.0,
            retry_backoff=2.0,
            max_delay=30.0,
        )
        mw = LLMErrorHandlingMiddleware(cfg)

        assert mw._compute_delay(0) == 1.0
        assert mw._compute_delay(1) == 2.0
        assert mw._compute_delay(2) == 4.0
        assert mw._compute_delay(3) == 8.0

    def test_delay_capped_at_max(self) -> None:
        cfg = LLMErrorHandlingConfig(
            retry_delay=1.0,
            retry_backoff=10.0,
            max_delay=5.0,
        )
        mw = LLMErrorHandlingMiddleware(cfg)

        assert mw._compute_delay(0) == 1.0
        assert mw._compute_delay(1) == 5.0  # capped
        assert mw._compute_delay(5) == 5.0  # still capped

    def test_should_retry_within_limit(self) -> None:
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(max_retries=3),
        )
        assert mw.should_retry(0) is True
        assert mw.should_retry(2) is True
        assert mw.should_retry(3) is False

    @pytest.mark.asyncio
    async def test_fallback_model_recorded(self) -> None:
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(
                fallback_model="gpt-4o-mini",
                max_retries=0,
            ),
        )
        result = mw.handle_error(RuntimeError("API error"), 0)
        assert result is not None
        assert result["fallback_model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_no_fallback_returns_none(self) -> None:
        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(
                fallback_model="",
                max_retries=0,
            ),
        )
        result = mw.handle_error(RuntimeError("API error"), 0)
        assert result is None


class TestMiddlewareIntegration:
    """Integration with MiddlewareBase interface."""

    @pytest.mark.asyncio
    async def test_on_model_call_closed_passes(self) -> None:
        """When circuit is CLOSED, on_model_call should not raise."""
        from agentscope.agent import Agent

        mw = LLMErrorHandlingMiddleware()
        agent = MagicMock(spec=Agent)
        agent.name = "test"

        called = False

        async def fake_next():
            nonlocal called
            called = True

        await mw.on_model_call(
            agent,
            {"messages": []},
            fake_next,
        )
        assert called is True

    @pytest.mark.asyncio
    async def test_on_model_call_open_blocks(self) -> None:
        """When circuit is OPEN, on_model_call should raise."""
        from agentscope.agent import Agent

        mw = LLMErrorHandlingMiddleware(
            LLMErrorHandlingConfig(circuit_breaker_threshold=1),
        )
        mw.record_failure()  # Open the circuit
        agent = MagicMock(spec=Agent)
        agent.name = "test"

        with pytest.raises(CircuitBreakerOpenError):
            await mw.on_model_call(
                agent,
                {"messages": []},
                AsyncMock(),
            )
