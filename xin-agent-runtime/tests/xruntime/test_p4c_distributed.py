# -*- coding: utf-8 -*-
"""TDD tests for P4-C: Distributed execution."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from xruntime._runtime._workflow._distributed import (
    Task,
    InMemoryTaskQueue,
    DistributedLock,
)


class TestTask:
    """Task dataclass."""

    def test_construction(self) -> None:
        t = Task(
            workflow_id="wf",
            step_id="s1",
            context={"x": 1},
        )
        assert t.task_id.startswith("task-")
        assert t.workflow_id == "wf"
        assert t.status == "PENDING"
        assert t.created_at > 0

    def test_default_context_empty(self) -> None:
        t = Task()
        assert t.context == {}

    def test_default_status_pending(self) -> None:
        assert Task().status == "PENDING"


class TestInMemoryTaskQueue:
    """InMemoryTaskQueue — enqueue/dequeue/ack/fail."""

    @pytest.mark.asyncio
    async def test_enqueue_and_dequeue(self) -> None:
        q = InMemoryTaskQueue()
        t = await q.enqueue("wf", "s1", {"x": 1})
        assert t.status == "PENDING"
        got = await q.dequeue()
        assert got is not None
        assert got.task_id == t.task_id
        assert got.status == "RUNNING"

    @pytest.mark.asyncio
    async def test_dequeue_empty_returns_none(self) -> None:
        q = InMemoryTaskQueue()
        assert await q.dequeue() is None

    @pytest.mark.asyncio
    async def test_ack_marks_complete(self) -> None:
        q = InMemoryTaskQueue()
        t = await q.enqueue("wf", "s1")
        await q.dequeue()
        assert await q.ack(t.task_id) is True
        got = await q.get_task(t.task_id)
        assert got.status == "COMPLETED"
        assert got.completed_at is not None

    @pytest.mark.asyncio
    async def test_ack_unknown_returns_false(self) -> None:
        q = InMemoryTaskQueue()
        assert await q.ack("nonexistent") is False

    @pytest.mark.asyncio
    async def test_fail_records_error(self) -> None:
        q = InMemoryTaskQueue()
        t = await q.enqueue("wf", "s1")
        await q.dequeue()
        assert await q.fail(t.task_id, "boom") is True
        got = await q.get_task(t.task_id)
        assert got.status == "FAILED"
        assert got.error == "boom"

    @pytest.mark.asyncio
    async def test_fail_unknown_returns_false(self) -> None:
        q = InMemoryTaskQueue()
        assert await q.fail("nonexistent", "err") is False

    @pytest.mark.asyncio
    async def test_fifo_ordering(self) -> None:
        q = InMemoryTaskQueue()
        t1 = await q.enqueue("wf", "s1")
        t2 = await q.enqueue("wf", "s2")
        got1 = await q.dequeue()
        got2 = await q.dequeue()
        assert got1.task_id == t1.task_id
        assert got2.task_id == t2.task_id

    @pytest.mark.asyncio
    async def test_pending_count(self) -> None:
        q = InMemoryTaskQueue()
        await q.enqueue("wf", "s1")
        await q.enqueue("wf", "s2")
        assert q.pending_count == 2
        await q.dequeue()
        assert q.pending_count == 1

    @pytest.mark.asyncio
    async def test_total_count(self) -> None:
        q = InMemoryTaskQueue()
        await q.enqueue("wf", "s1")
        await q.enqueue("wf", "s2")
        assert q.total_count == 2

    @pytest.mark.asyncio
    async def test_get_task(self) -> None:
        q = InMemoryTaskQueue()
        t = await q.enqueue("wf", "s1", {"k": "v"})
        got = await q.get_task(t.task_id)
        assert got is not None
        assert got.context["k"] == "v"


class TestDistributedLock:
    """DistributedLock — acquire/release."""

    @pytest.mark.asyncio
    async def test_acquire_and_release(self) -> None:
        lock = DistributedLock()
        assert await lock.acquire("k1") is True
        assert lock.is_held("k1") is True
        assert await lock.release("k1") is True
        assert lock.is_held("k1") is False

    @pytest.mark.asyncio
    async def test_re_acquire_after_release(self) -> None:
        lock = DistributedLock()
        await lock.acquire("k1")
        await lock.release("k1")
        assert await lock.acquire("k1") is True
        await lock.release("k1")

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        lock = DistributedLock()
        await lock.acquire("k1")
        assert (
            await lock.acquire(
                "k1",
                timeout=0.1,
            )
            is False
        )

    @pytest.mark.asyncio
    async def test_mutual_exclusion(self) -> None:
        lock = DistributedLock()
        await lock.acquire("k1")

        async def _try() -> bool:
            return await lock.acquire("k1", timeout=0.1)

        result = await asyncio.create_task(_try())
        assert result is False

    @pytest.mark.asyncio
    async def test_release_not_held_returns_false(self) -> None:
        lock = DistributedLock()
        assert await lock.release("k1") is False

    @pytest.mark.asyncio
    async def test_different_keys_independent(self) -> None:
        lock = DistributedLock()
        assert await lock.acquire("k1") is True
        assert await lock.acquire("k2") is True
        await lock.release("k1")
        await lock.release("k2")
