# -*- coding: utf-8 -*-
"""TDD tests for ConcurrencyPool (P4-A).

Covers global + per-agent parallelism control.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from xruntime._runtime._workflow._concurrency import (
    ConcurrencyPool,
    PoolSlot,
)


class TestConcurrencyPoolConstruction:
    """Construction and validation."""

    def test_construction_with_params(self) -> None:
        pool = ConcurrencyPool(
            max_concurrent=5,
            per_agent_limit=2,
            acquire_timeout=10.0,
        )
        assert pool.max_concurrent == 5
        assert pool.per_agent_limit == 2
        assert pool.active_count == 0

    def test_defaults(self) -> None:
        pool = ConcurrencyPool()
        assert pool.max_concurrent == 10
        assert pool.per_agent_limit == 3

    def test_invalid_params_raise(self) -> None:
        with pytest.raises(ValueError):
            ConcurrencyPool(max_concurrent=0)
        with pytest.raises(ValueError):
            ConcurrencyPool(per_agent_limit=-1)


class TestConcurrencyPoolAcquire:
    """Acquire / release behaviour."""

    @pytest.mark.asyncio
    async def test_acquire_and_release(self) -> None:
        pool = ConcurrencyPool(max_concurrent=2)
        async with pool.acquire(agent="a"):
            assert pool.active_count == 1
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_async_with_syntax(self) -> None:
        pool = ConcurrencyPool()
        slot = await pool.acquire(agent="a")
        async with slot:
            assert pool.active_count == 1
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_timeout_on_global_limit(self) -> None:
        pool = ConcurrencyPool(
            max_concurrent=1,
            acquire_timeout=0.1,
        )
        async with pool.acquire(agent="a"):
            with pytest.raises(TimeoutError):
                await pool.acquire(agent="b")

    @pytest.mark.asyncio
    async def test_concurrent_acquire_succeeds(self) -> None:
        pool = ConcurrencyPool(max_concurrent=3)
        s1 = await pool.acquire(agent="a")
        s2 = await pool.acquire(agent="b")
        s3 = await pool.acquire(agent="c")
        slots = [s1, s2, s3]
        assert pool.active_count == 3
        for s in slots:
            await s.__aexit__(None, None, None)
        assert pool.active_count == 0


class TestConcurrencyPoolGlobalLimit:
    """Global semaphore limits."""

    @pytest.mark.asyncio
    async def test_global_limit_enforced(self) -> None:
        pool = ConcurrencyPool(max_concurrent=2)
        s1 = await pool.acquire(agent="a")
        s2 = await pool.acquire(agent="a")
        await s1.__aenter__()
        await s2.__aenter__()
        assert pool.active_count == 2
        # Third should wait
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                pool.acquire(agent="a"),
                timeout=0.1,
            )
        await s1.__aexit__(None, None, None)
        await s2.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_release_allows_next(self) -> None:
        pool = ConcurrencyPool(max_concurrent=1)
        async with pool.acquire(agent="a"):
            pass
        # After release, new acquire should succeed
        async with pool.acquire(agent="a"):
            assert pool.active_count == 1

    @pytest.mark.asyncio
    async def test_over_limit_waits(self) -> None:
        pool = ConcurrencyPool(
            max_concurrent=1,
            acquire_timeout=0.2,
        )
        async with pool.acquire(agent="a"):
            start = time.monotonic()
            with pytest.raises(TimeoutError):
                await pool.acquire(agent="b")
            elapsed = time.monotonic() - start
            assert elapsed >= 0.15


class TestConcurrencyPoolPerAgent:
    """Per-agent independent semaphores."""

    @pytest.mark.asyncio
    async def test_per_agent_independent(self) -> None:
        pool = ConcurrencyPool(
            max_concurrent=10,
            per_agent_limit=1,
        )
        async with pool.acquire(agent="a"):
            # Agent b can still acquire (different semaphore)
            async with pool.acquire(agent="b"):
                assert pool.active_count == 2

    @pytest.mark.asyncio
    async def test_per_agent_limit_enforced(self) -> None:
        pool = ConcurrencyPool(
            max_concurrent=10,
            per_agent_limit=1,
            acquire_timeout=0.1,
        )
        async with pool.acquire(agent="a"):
            # Same agent, per-agent limit=1 → timeout
            with pytest.raises(TimeoutError):
                await pool.acquire(agent="a")

    @pytest.mark.asyncio
    async def test_different_agents_dont_block(self) -> None:
        pool = ConcurrencyPool(
            max_concurrent=5,
            per_agent_limit=1,
        )
        slots = await asyncio.gather(
            pool.acquire(agent="a"),
            pool.acquire(agent="b"),
            pool.acquire(agent="c"),
        )
        assert pool.active_count == 3
        for s in slots:
            await s.__aexit__(None, None, None)


class TestConcurrencyPoolMetrics:
    """active_count / waiting_count / utilization."""

    @pytest.mark.asyncio
    async def test_metrics(self) -> None:
        pool = ConcurrencyPool(max_concurrent=4)
        assert pool.utilization == 0.0
        async with pool.acquire(agent="a"):
            assert pool.active_count == 1
            assert pool.utilization == pytest.approx(0.25)
        assert pool.utilization == 0.0

    def test_utilization_with_zero_max_raises(self) -> None:
        """max_concurrent=0 在构造时就会 raise,不会到 utilization."""
        with pytest.raises(ValueError):
            ConcurrencyPool(max_concurrent=0)

    @pytest.mark.asyncio
    async def test_waiting_count(self) -> None:
        pool = ConcurrencyPool(
            max_concurrent=1,
            acquire_timeout=5.0,
        )
        async with pool.acquire(agent="a"):
            # Start a second acquire that will wait
            async def _waiter() -> None:
                slot = await pool.acquire(agent="b")
                async with slot:
                    pass

            task = asyncio.create_task(_waiter())
            await asyncio.sleep(0.1)  # let it start waiting
            assert pool.waiting_count >= 1
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
