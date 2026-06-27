# -*- coding: utf-8 -*-
"""Tests for SubAgent system."""
from __future__ import annotations

import asyncio

import pytest

from xruntime._runtime._subagents import (
    SubAgentExecutor,
    SubAgentResult,
    SubAgentSpec,
    SubAgentTask,
    TaskTool,
)


class TestSubAgentSpec:
    """Spec model tests."""

    def test_defaults(self) -> None:
        s = SubAgentSpec(
            name="researcher",
            description="Research specialist",
        )
        assert s.name == "researcher"
        assert s.system_prompt == "You are a helpful assistant."
        assert s.max_turns == 10
        assert s.allowed_tools == []

    def test_full(self) -> None:
        s = SubAgentSpec(
            name="coder",
            description="Code specialist",
            system_prompt="You write clean code.",
            model_config_name="gpt-4o",
            allowed_tools=["bash", "read_file"],
            max_turns=15,
        )
        assert s.model_config_name == "gpt-4o"
        assert "bash" in s.allowed_tools
        assert s.max_turns == 15


class TestSubAgentTask:
    """Task model tests."""

    def test_defaults(self) -> None:
        t = SubAgentTask(
            spec_name="researcher",
            objective="Find info about Python",
        )
        assert t.task_id != ""
        assert t.constraints == []
        assert t.input_context == ""
        assert t.expected_output == ""

    def test_full(self) -> None:
        t = SubAgentTask(
            spec_name="coder",
            objective="Fix the bug",
            constraints=["Don't touch auth.py"],
            input_context="Bug is in line 42",
            expected_output="Fixed code",
        )
        assert len(t.constraints) == 1
        assert "Bug" in t.input_context


class TestSubAgentResult:
    """Result model tests."""

    def test_defaults(self) -> None:
        r = SubAgentResult(task_id="t1")
        assert r.success is True
        assert r.summary == ""
        assert r.findings == []
        assert r.duration_seconds == 0.0

    def test_failure(self) -> None:
        r = SubAgentResult(
            task_id="t1",
            success=False,
            errors=["Timeout"],
        )
        assert r.success is False
        assert "Timeout" in r.errors


class TestSubAgentExecutor:
    """Executor behaviour tests."""

    @pytest.fixture
    def specs(self) -> list[SubAgentSpec]:
        return [
            SubAgentSpec(
                name="researcher",
                description="Research specialist",
            ),
            SubAgentSpec(
                name="coder",
                description="Code specialist",
            ),
        ]

    @pytest.fixture
    def executor(
        self,
        specs: list[SubAgentSpec],
    ) -> SubAgentExecutor:
        return SubAgentExecutor(specs, max_concurrent=2)

    @pytest.mark.asyncio
    async def test_execute_unknown_spec(
        self,
        executor: SubAgentExecutor,
    ) -> None:
        """Unknown spec returns failure result."""
        task = SubAgentTask(
            spec_name="nonexistent",
            objective="test",
        )
        result = await executor.execute(task)
        assert result.success is False
        assert "nonexistent" in result.errors[0]

    @pytest.mark.asyncio
    async def test_execute_mock_runner(
        self,
        executor: SubAgentExecutor,
    ) -> None:
        """Default mock runner returns success."""
        task = SubAgentTask(
            spec_name="researcher",
            objective="Find Python info",
        )
        result = await executor.execute(task)
        assert result.success is True
        assert "Python" in result.summary
        assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_execute_custom_runner(
        self,
        executor: SubAgentExecutor,
    ) -> None:
        """Custom runner is called correctly."""

        async def my_runner(
            spec: SubAgentSpec,
            task: SubAgentTask,
        ) -> SubAgentResult:
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary=f"Done: {task.objective}",
                findings=["finding1", "finding2"],
                token_usage=100,
            )

        task = SubAgentTask(
            spec_name="coder",
            objective="Write tests",
        )
        result = await executor.execute(task, runner=my_runner)
        assert result.success is True
        assert "Done" in result.summary
        assert len(result.findings) == 2
        assert result.token_usage == 100

    @pytest.mark.asyncio
    async def test_execute_sync_runner(
        self,
        executor: SubAgentExecutor,
    ) -> None:
        """Sync runner also works."""

        def sync_runner(
            spec: SubAgentSpec,
            task: SubAgentTask,
        ) -> SubAgentResult:
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary="Sync result",
            )

        task = SubAgentTask(
            spec_name="researcher",
            objective="test",
        )
        result = await executor.execute(task, runner=sync_runner)
        assert result.success is True
        assert result.summary == "Sync result"

    @pytest.mark.asyncio
    async def test_execute_batch(
        self,
        executor: SubAgentExecutor,
    ) -> None:
        """Batch executes all tasks."""
        tasks = [
            SubAgentTask(
                spec_name="researcher",
                objective=f"Task {i}",
            )
            for i in range(4)
        ]
        results = await executor.execute_batch(tasks)
        assert len(results) == 4
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_execute_batch_preserves_order(
        self,
        executor: SubAgentExecutor,
    ) -> None:
        """Results are in the same order as input tasks."""
        tasks = [
            SubAgentTask(
                spec_name="researcher",
                objective=f"Task-{i}",
            )
            for i in range(3)
        ]
        results = await executor.execute_batch(tasks)
        for i, r in enumerate(results):
            assert f"Task-{i}" in r.summary

    @pytest.mark.asyncio
    async def test_concurrency_limit(
        self,
        specs: list[SubAgentSpec],
    ) -> None:
        """Semaphore limits concurrent executions."""
        executor = SubAgentExecutor(
            specs,
            max_concurrent=2,
        )
        current = 0
        peak = 0

        async def tracking_runner(
            spec: SubAgentSpec,
            task: SubAgentTask,
        ) -> SubAgentResult:
            nonlocal current, peak
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.05)
            current -= 1
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
            )

        tasks = [
            SubAgentTask(
                spec_name="researcher",
                objective=f"Task {i}",
            )
            for i in range(5)
        ]
        await executor.execute_batch(tasks, runner=tracking_runner)
        assert peak <= 2

    def test_stats(
        self,
        executor: SubAgentExecutor,
    ) -> None:
        """Stats property returns counters."""
        stats = executor.stats
        assert stats["total_executed"] == 0
        assert stats["total_succeeded"] == 0
        assert stats["total_failed"] == 0

    @pytest.mark.asyncio
    async def test_stats_updated(
        self,
        executor: SubAgentExecutor,
    ) -> None:
        """Stats are updated after execution."""
        await executor.execute(
            SubAgentTask(
                spec_name="researcher",
                objective="ok",
            )
        )
        await executor.execute(
            SubAgentTask(
                spec_name="nonexistent",
                objective="fail",
            )
        )
        stats = executor.stats
        assert stats["total_executed"] == 2
        assert stats["total_succeeded"] == 1
        assert stats["total_failed"] == 1

    def test_add_spec(self, executor: SubAgentExecutor) -> None:
        """add_spec adds a new spec."""
        new_spec = SubAgentSpec(
            name="writer",
            description="Writing specialist",
        )
        executor.add_spec(new_spec)
        assert "writer" in executor.specs

    def test_reset_stats(self, executor: SubAgentExecutor) -> None:
        """reset_stats zeroes all counters."""
        executor._total_executed = 10
        executor._total_succeeded = 8
        executor._total_failed = 2
        executor.reset_stats()
        assert executor.stats["total_executed"] == 0


class TestTaskTool:
    """TaskTool tests."""

    @pytest.mark.asyncio
    async def test_call_success(self) -> None:
        """Tool delegates to executor and returns summary."""
        specs = [
            SubAgentSpec(
                name="researcher",
                description="Research",
            )
        ]
        executor = SubAgentExecutor(specs)
        tool = TaskTool(executor)

        result = await tool(
            subagent="researcher",
            description="Find Python info",
        )
        assert result["success"] is True
        assert "Python" in result["summary"]

    @pytest.mark.asyncio
    async def test_call_unknown_agent(self) -> None:
        """Unknown agent returns error."""
        executor = SubAgentExecutor([])
        tool = TaskTool(executor)

        result = await tool(
            subagent="nonexistent",
            description="test",
        )
        assert result["success"] is False
        assert "errors" in result
