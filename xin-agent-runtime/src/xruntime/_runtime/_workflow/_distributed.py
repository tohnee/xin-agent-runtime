# -*- coding: utf-8 -*-
"""P4-C: Distributed execution — TaskQueue + DistributedLock."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    """A unit of work for distributed execution.

    Args:
        task_id (`str`): Unique id (auto-generated).
        workflow_id (`str`): The workflow id.
        step_id (`str`): The step id.
        context (`dict`): Step context.
        status (`str`): PENDING / RUNNING / COMPLETED / FAILED.
        error (`str`): Error message if failed.
        created_at (`float`): Creation timestamp.
        completed_at (`float | None`): Completion timestamp.
    """

    task_id: str = field(
        default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}",
    )
    workflow_id: str = ""
    step_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    status: str = "PENDING"
    error: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None


class InMemoryTaskQueue:
    """In-memory FIFO task queue for distributed execution."""

    def __init__(self) -> None:
        self._queue: list[Task] = []
        self._tasks: dict[str, Task] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        workflow_id: str,
        step_id: str,
        context: dict[str, Any] | None = None,
    ) -> Task:
        """Add a task to the queue.

        Returns:
            `Task`: The created task.
        """
        task = Task(
            workflow_id=workflow_id,
            step_id=step_id,
            context=context or {},
        )
        async with self._lock:
            self._queue.append(task)
            self._tasks[task.task_id] = task
        return task

    async def dequeue(self) -> Task | None:
        """Remove and return the next task.

        Returns:
            `Task | None`: The next task, or ``None`` if empty.
        """
        async with self._lock:
            if not self._queue:
                return None
            task = self._queue.pop(0)
            task.status = "RUNNING"
            return task

    async def ack(self, task_id: str) -> bool:
        """Mark a task as completed.

        Returns:
            `bool`: True if acknowledged, False if not found.
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.status = "COMPLETED"
            task.completed_at = time.time()
            return True

    async def fail(
        self,
        task_id: str,
        error: str,
    ) -> bool:
        """Mark a task as failed.

        Returns:
            `bool`: True if marked, False if not found.
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.status = "FAILED"
            task.error = error
            task.completed_at = time.time()
            return True

    async def get_task(self, task_id: str) -> Task | None:
        """Look up a task by id."""
        return self._tasks.get(task_id)

    @property
    def pending_count(self) -> int:
        """Return number of pending tasks."""
        return len(self._queue)

    @property
    def total_count(self) -> int:
        """Return total number of tasks (all statuses)."""
        return len(self._tasks)


class DistributedLock:
    """In-process distributed lock (asyncio-based).

    Designed for Redis extension.  Uses ``asyncio.Lock`` per key.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._holders: dict[str, bool] = {}

    async def acquire(
        self,
        key: str,
        timeout: float = 30.0,
    ) -> bool:
        """Acquire a lock for the given key.

        Args:
            key (`str`): Lock key.
            timeout (`float`): Timeout in seconds.

        Returns:
            `bool`: True if acquired, False on timeout.
        """
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        try:
            await asyncio.wait_for(
                self._locks[key].acquire(),
                timeout=timeout,
            )
            self._holders[key] = True
            return True
        except TimeoutError:
            return False

    async def release(self, key: str) -> bool:
        """Release a lock.

        Returns:
            `bool`: True if released, False if not held.
        """
        if not self._holders.get(key, False):
            return False
        self._locks[key].release()
        self._holders[key] = False
        return True

    def is_held(self, key: str) -> bool:
        """Check if a lock is currently held."""
        return self._holders.get(key, False)


__all__ = [
    "Task",
    "InMemoryTaskQueue",
    "DistributedLock",
]
