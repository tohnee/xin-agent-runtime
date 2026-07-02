# -*- coding: utf-8 -*-
"""TDD tests for TokenBucketRateLimiter (P4-A).

Covers the token-bucket rate limiter used by the workflow runtime to
throttle concurrent agent operations:

* construction — valid params and validation of invalid params
* acquire — token available, blocks when empty, timeout returns False
* burst — initial burst capacity, depletion after burst
* refill — tokens replenish over time, re-acquire after refill
* concurrency — asyncio-safe concurrent acquisition
"""
from __future__ import annotations

import asyncio
import time

import pytest

from xruntime._runtime._workflow._rate_limiter import (
    TokenBucketRateLimiter,
)


# ── 1. Construction ────────────────────────────────────────────


class TestRateLimiterConstruction:
    """TokenBucketRateLimiter — construction and validation."""

    def test_construction_with_rate_and_burst(self) -> None:
        """Construction with valid rate/burst sets attributes."""
        rl = TokenBucketRateLimiter(rate=50.0, burst=10)
        assert rl.rate == 50.0
        assert rl.burst == 10
        # bucket starts full
        assert rl.available_tokens == 10.0

    def test_invalid_params_raise(self) -> None:
        """rate <= 0 or burst <= 0 raises ValueError."""
        with pytest.raises(ValueError):
            TokenBucketRateLimiter(rate=0.0, burst=10)
        with pytest.raises(ValueError):
            TokenBucketRateLimiter(rate=-1.0, burst=10)
        with pytest.raises(ValueError):
            TokenBucketRateLimiter(rate=100.0, burst=0)
        with pytest.raises(ValueError):
            TokenBucketRateLimiter(rate=100.0, burst=-5)


# ── 2. Acquire ────────────────────────────────────────────────


class TestRateLimiterAcquire:
    """TokenBucketRateLimiter — acquire behaviour."""

    @pytest.mark.asyncio
    async def test_acquire_token_when_available(self) -> None:
        """acquire returns True immediately when a token is available."""
        rl = TokenBucketRateLimiter(rate=100.0, burst=5)
        result = await rl.acquire(timeout=1.0)
        assert result is True
        # one token consumed (allow for tiny refill since acquire
        # and the property read are not instantaneous)
        assert 4.0 <= rl.available_tokens < 5.0

    @pytest.mark.asyncio
    async def test_acquire_blocks_when_empty(self) -> None:
        """acquire blocks until a token refills, then returns True."""
        # rate=20 => 1 token / 0.05s; burst=1 drains immediately
        rl = TokenBucketRateLimiter(rate=20.0, burst=1)
        await rl.acquire(timeout=1.0)  # drain the bucket
        assert rl.available_tokens < 1.0
        start = time.monotonic()
        # generous timeout — should succeed after refill
        result = await rl.acquire(timeout=1.0)
        elapsed = time.monotonic() - start
        assert result is True
        # must have blocked (refill takes ~0.05s)
        assert elapsed >= 0.01

    @pytest.mark.asyncio
    async def test_acquire_timeout_returns_false(self) -> None:
        """acquire returns False when timeout elapses with no token."""
        # rate=1 => 1 token / 1s; burst=1 drains immediately
        rl = TokenBucketRateLimiter(rate=1.0, burst=1)
        await rl.acquire(timeout=1.0)  # drain the bucket
        # tiny timeout — cannot refill in time
        result = await rl.acquire(timeout=0.01)
        assert result is False


# ── 3. Burst capacity ─────────────────────────────────────────


class TestRateLimiterBurst:
    """TokenBucketRateLimiter — burst capacity."""

    @pytest.mark.asyncio
    async def test_burst_capacity_allows_initial_burst(self) -> None:
        """Initial burst capacity allows burst acquires in quick
        succession."""
        rl = TokenBucketRateLimiter(rate=1.0, burst=5)
        # all 5 should succeed without waiting
        for _ in range(5):
            assert await rl.acquire(timeout=0.01) is True

    @pytest.mark.asyncio
    async def test_tokens_deplete_after_burst(self) -> None:
        """After exhausting burst, the next acquire fails on short
        timeout."""
        rl = TokenBucketRateLimiter(rate=1.0, burst=3)
        for _ in range(3):
            assert await rl.acquire(timeout=0.01) is True
        # bucket depleted — short timeout fails
        assert await rl.acquire(timeout=0.01) is False
        assert rl.available_tokens < 1.0


# ── 4. Refill ─────────────────────────────────────────────────


class TestRateLimiterRefill:
    """TokenBucketRateLimiter — token refill over time."""

    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self) -> None:
        """Tokens replenish based on elapsed wall-clock time."""
        rl = TokenBucketRateLimiter(rate=1000.0, burst=5)
        # drain the bucket
        for _ in range(5):
            await rl.acquire(timeout=0.01)
        assert rl.available_tokens < 1.0
        # wait long enough for refill at rate=1000/s
        await asyncio.sleep(0.02)
        assert rl.available_tokens > 0.0

    @pytest.mark.asyncio
    async def test_after_refill_can_acquire_again(self) -> None:
        """After refill elapses, acquire succeeds immediately."""
        rl = TokenBucketRateLimiter(rate=100.0, burst=1)
        await rl.acquire(timeout=1.0)  # drain
        # wait for at least one token (0.01s at rate=100)
        await asyncio.sleep(0.03)
        # should succeed quickly
        start = time.monotonic()
        assert await rl.acquire(timeout=1.0) is True
        assert time.monotonic() - start < 0.05


# ── 5. Concurrency ─────────────────────────────────────────────


class TestRateLimiterConcurrency:
    """TokenBucketRateLimiter — async concurrency safety."""

    @pytest.mark.asyncio
    async def test_concurrent_acquire_is_thread_safe(self) -> None:
        """Concurrent acquires never exceed burst capacity.

        With burst=3 and a slow refill (rate=1/s), exactly 3 of 5
        concurrent acquires should succeed within a short timeout and
        2 should time out — proving the asyncio.Lock serialises
        access and prevents over-issuance.
        """
        rl = TokenBucketRateLimiter(rate=1.0, burst=3)
        results = await asyncio.gather(
            *[rl.acquire(timeout=0.05) for _ in range(5)]
        )
        successes = sum(1 for r in results if r is True)
        assert successes == 3
        # remaining must have timed out
        assert successes < 5
