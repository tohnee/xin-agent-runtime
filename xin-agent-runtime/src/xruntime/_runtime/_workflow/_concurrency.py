# -*- coding: utf-8 -*-
"""ConcurrencyPool — global + per-agent parallelism control.

Limits the number of concurrently executing workflow steps using
two levels of ``asyncio.Semaphore``:

* **Global limit** — ``max_concurrent`` caps total parallel steps.
* **Per-agent limit** — ``per_agent_limit`` caps parallel steps
  for a single agent name.

Acquiring a slot returns a :class:`PoolSlot` that supports both
``await`` and ``async with`` usage::

    pool = ConcurrencyPool(max_concurrent=10, per_agent_limit=3)

    # Pattern 1: async with directly
    async with pool.acquire(agent="coder"):
        ...

    # Pattern 2: await then async with
    slot = await pool.acquire(agent="coder")
    async with slot:
        ...
"""
from __future__ import annotations

import asyncio
from typing import Any


class PoolSlot:
    """Async slot that supports both ``await`` and ``async with``.

    The semaphores are acquired during ``await`` (or
    ``__aenter__``).  On ``__aexit__`` both semaphores are released
    and the pool's active count is decremented.

    Args:
        pool (`ConcurrencyPool`): The owning pool.
    """

    def __init__(self, pool: "ConcurrencyPool") -> None:
        self._pool = pool
        self._global_acquired = False
        self._agent_acquired = False
        self._agent: str = ""
        self._agent_sem: asyncio.Semaphore | None = None
        self._entered = False

    def _set_agent(self, agent: str) -> None:
        self._agent = agent
        self._agent_sem = self._pool._get_agent_sem(agent)

    def __await__(self) -> Any:
        async def _await() -> "PoolSlot":
            await self._acquire_both()
            self._pool._active += 1
            self._entered = True
            return self

        return _await().__await__()

    async def _acquire_both(self) -> None:
        """Acquire both global and agent semaphores."""
        assert self._agent_sem is not None

        try:
            await asyncio.wait_for(
                self._pool._global_sem.acquire(),
                timeout=self._pool._timeout,
            )
        except TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for global concurrency "
                f"slot (limit={self._pool._max_concurrent})",
            )

        self._global_acquired = True

        try:
            await asyncio.wait_for(
                self._agent_sem.acquire(),
                timeout=self._pool._timeout,
            )
        except TimeoutError:
            self._pool._global_sem.release()
            self._global_acquired = False
            raise TimeoutError(
                f"Timed out waiting for agent '{self._agent}' "
                f"concurrency slot "
                f"(limit={self._pool._per_agent_limit})",
            )

        self._agent_acquired = True

    async def __aenter__(self) -> "PoolSlot":
        if not self._global_acquired:
            await self._acquire_both()
        if not self._entered:
            self._pool._active += 1
            self._entered = True
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._entered:
            if self._agent_acquired:
                assert self._agent_sem is not None
                self._agent_sem.release()
                self._agent_acquired = False
            if self._global_acquired:
                self._pool._global_sem.release()
                self._global_acquired = False
            self._pool._active -= 1
            self._entered = False


class ConcurrencyPool:
    """Global + per-agent concurrency limiter.

    Args:
        max_concurrent (`int`):
            Maximum number of steps executing globally.
        per_agent_limit (`int`):
            Maximum parallel steps per agent name.
        acquire_timeout (`float`):
            Seconds to wait for a slot before raising
            :class:`TimeoutError`.
    """

    def __init__(
        self,
        max_concurrent: int = 10,
        per_agent_limit: int = 3,
        acquire_timeout: float = 30.0,
    ) -> None:
        if max_concurrent <= 0:
            raise ValueError(
                f"max_concurrent must be > 0, got {max_concurrent}",
            )
        if per_agent_limit <= 0:
            raise ValueError(
                f"per_agent_limit must be > 0, " f"got {per_agent_limit}",
            )
        self._max_concurrent = max_concurrent
        self._per_agent_limit = per_agent_limit
        self._timeout = acquire_timeout
        self._global_sem = asyncio.Semaphore(max_concurrent)
        self._agent_sems: dict[str, asyncio.Semaphore] = {}
        self._active = 0

    @property
    def max_concurrent(self) -> int:
        """Return the global concurrency limit."""
        return self._max_concurrent

    @property
    def per_agent_limit(self) -> int:
        """Return the per-agent concurrency limit."""
        return self._per_agent_limit

    @property
    def active_count(self) -> int:
        """Return the number of currently active slots."""
        return self._active

    @property
    def waiting_count(self) -> int:
        """Return the number of waiters on the global semaphore."""
        return len(self._global_sem._waiters)

    @property
    def utilization(self) -> float:
        """Return utilization ratio (0.0 to 1.0)."""
        return self._active / self._max_concurrent

    def _get_agent_sem(self, agent: str) -> asyncio.Semaphore:
        """Get or create the per-agent semaphore."""
        if agent not in self._agent_sems:
            self._agent_sems[agent] = asyncio.Semaphore(
                self._per_agent_limit,
            )
        return self._agent_sems[agent]

    def acquire(self, agent: str) -> PoolSlot:
        """Create a slot for the given agent.

        The returned :class:`PoolSlot` supports both ``await`` and
        ``async with``.  Semaphores are acquired on first
        ``await`` or ``__aenter__``.

        Args:
            agent (`str`): The agent name.

        Returns:
            `PoolSlot`: A slot that acquires semaphores on
            ``await`` / ``__aenter__`` and releases on
            ``__aexit__``.
        """
        slot = PoolSlot(self)
        slot._set_agent(agent)
        return slot


__all__ = ["ConcurrencyPool", "PoolSlot"]
