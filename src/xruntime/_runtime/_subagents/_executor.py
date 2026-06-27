# -*- coding: utf-8 -*-
"""SubAgentExecutor — parallel execution with concurrency control."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ._models import SubAgentResult, SubAgentSpec, SubAgentTask


class SubAgentExecutor:
    """Executes sub-agent tasks with concurrency control.

    Args:
        specs (`list[SubAgentSpec]`): Available sub-agent specs.
        max_concurrent (`int`): Max parallel executions.
    """

    def __init__(
        self,
        specs: list[SubAgentSpec],
        max_concurrent: int = 3,
    ) -> None:
        """Initialize the executor."""
        self._specs: dict[str, SubAgentSpec] = {s.name: s for s in specs}
        self._max_concurrent = max_concurrent
        self._semaphore: asyncio.Semaphore | None = None
        self._total_executed: int = 0
        self._total_succeeded: int = 0
        self._total_failed: int = 0

    @property
    def specs(self) -> dict[str, SubAgentSpec]:
        """Available sub-agent specs."""
        return self._specs

    @property
    def stats(self) -> dict[str, int]:
        """Execution statistics."""
        return {
            "total_executed": self._total_executed,
            "total_succeeded": self._total_succeeded,
            "total_failed": self._total_failed,
        }

    def add_spec(self, spec: SubAgentSpec) -> None:
        """Add or replace a sub-agent spec.

        Args:
            spec (`SubAgentSpec`): The spec to add.
        """
        self._specs[spec.name] = spec

    async def execute(
        self,
        task: SubAgentTask,
        runner: Any | None = None,
    ) -> SubAgentResult:
        """Execute a single sub-agent task.

        Args:
            task (`SubAgentTask`): The task to execute.
            runner (`Any | None`): Optional callable that
                takes ``(spec, task)`` and returns a
                ``SubAgentResult``. If None, a mock runner
                is used (for testing).

        Returns:
            `SubAgentResult`: The execution result.
        """
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(
                self._max_concurrent,
            )

        spec = self._specs.get(task.spec_name)
        self._total_executed += 1

        if spec is None:
            self._total_failed += 1
            return SubAgentResult(
                task_id=task.task_id,
                success=False,
                errors=[f"Unknown sub-agent spec: {task.spec_name}"],
            )
        start = time.time()

        async with self._semaphore:
            if runner is not None:
                result = await self._invoke_runner(
                    runner,
                    spec,
                    task,
                )
            else:
                result = SubAgentResult(
                    task_id=task.task_id,
                    success=True,
                    summary=f"Mock execution of '{task.objective}'",
                )

        result.duration_seconds = time.time() - start

        if result.success:
            self._total_succeeded += 1
        else:
            self._total_failed += 1

        return result

    async def execute_batch(
        self,
        tasks: list[SubAgentTask],
        runner: Any | None = None,
    ) -> list[SubAgentResult]:
        """Execute multiple tasks in parallel batches.

        Tasks are split into batches of ``max_concurrent``.
        Each batch runs in parallel; batches run sequentially.

        Args:
            tasks (`list[SubAgentTask]`): Tasks to execute.
            runner (`Any | None`): Optional runner callable.

        Returns:
            `list[SubAgentResult]`: Results in the same order
            as the input tasks.
        """
        results: list[SubAgentResult] = []
        for i in range(
            0,
            len(tasks),
            self._max_concurrent,
        ):
            batch = tasks[i : i + self._max_concurrent]
            batch_results = await asyncio.gather(
                *[self.execute(t, runner) for t in batch],
            )
            results.extend(batch_results)
        return results

    def reset_stats(self) -> None:
        """Reset execution statistics."""
        self._total_executed = 0
        self._total_succeeded = 0
        self._total_failed = 0

    @staticmethod
    async def _invoke_runner(
        runner: Any,
        spec: SubAgentSpec,
        task: SubAgentTask,
    ) -> SubAgentResult:
        """Invoke a runner callable, handling sync/async.

        Args:
            runner: The runner callable.
            spec: The sub-agent spec.
            task: The task.

        Returns:
            `SubAgentResult`: The result.
        """
        import inspect

        if inspect.iscoroutinefunction(runner):
            return await runner(spec, task)
        return runner(spec, task)
