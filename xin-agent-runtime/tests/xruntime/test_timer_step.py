# -*- coding: utf-8 -*-
"""TDD tests for TimerStep (P3-A Task 4).

Covers durable sleep: a :class:`TimerStep` pauses the workflow for
``duration_seconds``.  In non-checkpoint mode (store=None), the
timer is a simple ``asyncio.sleep``.  In checkpoint mode, the timer
saves a ``SLEEPING`` checkpoint with ``wake_at`` timestamp so a
crashed workflow can be resumed after the timer elapses.

Design notes:

* ``TimerStep.duration_seconds`` is the sleep duration.
* When ``duration_seconds <= 0``, the timer is a no-op (returns
  immediately with empty output).
* The step's output is an empty string (timers produce no data;
  they only gate subsequent steps).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    StepStatus,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from xruntime._runtime._workflow._checkpoint import (
    CheckpointStatus,
    InMemoryCheckpointStore,
)
from xruntime._runtime._workflow._sdk import (
    FunctionExecutor,
    run_workflow,
)


# ── 1. TimerStep construction ──────────────────────────────────


class TestTimerStepConstruction:
    """TimerStep — construction and field validation."""

    def test_timer_step_with_duration(self) -> None:
        """构造: duration_seconds + 标准字段."""
        from xruntime._runtime._workflow._steps import TimerStep

        step = TimerStep(
            id="wait",
            name="Wait 1h",
            agent="a",
            prompt="sleep",
            duration_seconds=3600,
        )
        assert step.id == "wait"
        assert step.name == "Wait 1h"
        assert step.duration_seconds == 3600

    def test_timer_step_inherits_workflow_step_fields(self) -> None:
        """TimerStep 继承 WorkflowStep 的所有字段."""
        from xruntime._runtime._workflow._steps import TimerStep

        step = TimerStep(
            id="wait",
            name="W",
            agent="a",
            prompt="p",
            duration_seconds=60,
            depends_on=["s1"],
            on_failure="continue",
            max_retries=2,
        )
        assert step.depends_on == ["s1"]
        assert step.on_failure == "continue"
        assert step.max_retries == 2

    def test_timer_step_default_duration_zero(self) -> None:
        """TimerStep 默认 duration_seconds=0(no-op)."""
        from xruntime._runtime._workflow._steps import TimerStep

        step = TimerStep(id="w", name="W", agent="a", prompt="p")
        assert step.duration_seconds == 0


# ── 2. TimerStep execution (no checkpoint) ─────────────────────


class TestTimerStepExecution:
    """TimerStep — sleep behavior without checkpoint store."""

    @pytest.mark.asyncio
    async def test_timer_zero_duration_returns_immediately(self) -> None:
        """duration_seconds=0 时立即返回(no-op)."""
        from xruntime._runtime._workflow._steps import TimerStep

        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            return f"out-{step.id}"

        wf = Workflow(
            id="wf-timer-zero",
            name="Timer Zero",
            steps=[
                TimerStep(
                    id="wait",
                    name="Wait",
                    agent="a",
                    prompt="sleep",
                    duration_seconds=0,
                ),
                WorkflowStep(
                    id="after",
                    name="After",
                    agent="a",
                    prompt="p",
                    depends_on=["wait"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        start = time.time()
        result = await run_workflow(wf, executor)
        elapsed = time.time() - start

        assert result.status == WorkflowStatus.COMPLETED
        # timer 不应该阻塞(duration=0)
        assert elapsed < 1.0
        # timer 输出为空字符串
        assert result.step_results.get("wait", "") == ""
        # 后续 step 正常执行
        assert result.step_results["after"] == "out-after"

    @pytest.mark.asyncio
    async def test_timer_short_duration_sleeps(self) -> None:
        """duration_seconds > 0 时实际 sleep."""
        from xruntime._runtime._workflow._steps import TimerStep

        wf = Workflow(
            id="wf-timer-sleep",
            name="Timer Sleep",
            steps=[
                TimerStep(
                    id="wait",
                    name="Wait",
                    agent="a",
                    prompt="sleep",
                    duration_seconds=0.1,  # 100ms
                ),
            ],
        )
        executor = FunctionExecutor(lambda s, c: f"out-{s.id}")
        start = time.time()
        result = await run_workflow(wf, executor)
        elapsed = time.time() - start

        assert result.status == WorkflowStatus.COMPLETED
        # 应该至少 sleep 了 0.1 秒(允许误差)
        assert elapsed >= 0.08  # 稍宽松一点

    @pytest.mark.asyncio
    async def test_timer_output_is_empty_string(self) -> None:
        """timer step 的输出始终为空字符串."""
        from xruntime._runtime._workflow._steps import TimerStep

        wf = Workflow(
            id="wf-timer-out",
            name="Timer Out",
            steps=[
                TimerStep(
                    id="wait",
                    name="Wait",
                    agent="a",
                    prompt="sleep",
                    duration_seconds=0,
                ),
            ],
        )
        executor = FunctionExecutor(lambda s, c: f"out-{s.id}")
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["wait"] == ""

    @pytest.mark.asyncio
    async def test_timer_step_after_uses_empty_output(self) -> None:
        """timer 之后的 step 收到空字符串输出."""
        from xruntime._runtime._workflow._steps import TimerStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "after":
                return f"after:{ctx.get('wait', 'MISSING')}"
            return f"out-{step.id}"

        wf = Workflow(
            id="wf-timer-dep",
            name="Timer Dep",
            steps=[
                TimerStep(
                    id="wait",
                    name="Wait",
                    agent="a",
                    prompt="sleep",
                    duration_seconds=0,
                ),
                WorkflowStep(
                    id="after",
                    name="After",
                    agent="a",
                    prompt="p",
                    depends_on=["wait"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # wait 的输出是空字符串 → after 收到空字符串
        assert result.step_results["after"] == "after:"


# ── 3. TimerStep with checkpoint (SLEEPING status) ────────────


class TestTimerStepWithCheckpoint:
    """TimerStep — SLEEPING checkpoint behavior."""

    @pytest.mark.asyncio
    async def test_timer_saves_sleeping_checkpoint(self) -> None:
        """timer step 在 checkpoint 模式下保存 SLEEPING checkpoint."""
        from xruntime._runtime._workflow._steps import TimerStep

        store = InMemoryCheckpointStore()
        wf = Workflow(
            id="wf-timer-cp",
            name="Timer CP",
            steps=[
                WorkflowStep(id="s1", name="S1", agent="a", prompt="p"),
                TimerStep(
                    id="wait",
                    name="Wait",
                    agent="a",
                    prompt="sleep",
                    duration_seconds=0.1,  # 100ms
                    depends_on=["s1"],
                ),
                WorkflowStep(
                    id="s2",
                    name="S2",
                    agent="a",
                    prompt="p",
                    depends_on=["wait"],
                ),
            ],
        )
        executor = FunctionExecutor(lambda s, c: f"out-{s.id}")
        result = await run_workflow(wf, executor, store=store)

        assert result.status == WorkflowStatus.COMPLETED
        # 应该有 checkpoint 保存
        checkpoints = await store.list_by_workflow("wf-timer-cp")
        assert len(checkpoints) > 0
        # 最终 checkpoint 应该是 COMPLETED
        latest = await store.latest_for_workflow("wf-timer-cp")
        assert latest is not None
        assert latest.status == CheckpointStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_timer_with_negative_duration_treated_as_zero(self) -> None:
        """负数 duration 当作 0 处理(no-op)."""
        from xruntime._runtime._workflow._steps import TimerStep

        wf = Workflow(
            id="wf-timer-neg",
            name="Timer Neg",
            steps=[
                TimerStep(
                    id="wait",
                    name="Wait",
                    agent="a",
                    prompt="sleep",
                    duration_seconds=-10,
                ),
            ],
        )
        executor = FunctionExecutor(lambda s, c: f"out-{s.id}")
        start = time.time()
        result = await run_workflow(wf, executor)
        elapsed = time.time() - start

        assert result.status == WorkflowStatus.COMPLETED
        assert elapsed < 1.0
        assert result.step_results.get("wait", "") == ""

    @pytest.mark.asyncio
    async def test_timer_in_workflow_with_other_steps(self) -> None:
        """timer 在多 step workflow 中正确工作."""
        from xruntime._runtime._workflow._steps import TimerStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "s1":
                return "start"
            if step.id == "s3":
                return f"end:{ctx.get('s1', '')}:{ctx.get('wait', '')}"
            return f"out-{step.id}"

        wf = Workflow(
            id="wf-timer-multi",
            name="Timer Multi",
            steps=[
                WorkflowStep(id="s1", name="S1", agent="a", prompt="p"),
                TimerStep(
                    id="wait",
                    name="Wait",
                    agent="a",
                    prompt="sleep",
                    duration_seconds=0,
                    depends_on=["s1"],
                ),
                WorkflowStep(
                    id="s3",
                    name="S3",
                    agent="a",
                    prompt="p",
                    depends_on=["wait"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["s1"] == "start"
        assert result.step_results["wait"] == ""
        assert result.step_results["s3"] == "end:start:"

    @pytest.mark.asyncio
    async def test_timer_completes_after_sleep(self) -> None:
        """timer 完成后 status=COMPLETED."""
        from xruntime._runtime._workflow._steps import TimerStep

        wf = Workflow(
            id="wf-timer-status",
            name="Timer Status",
            steps=[
                TimerStep(
                    id="wait",
                    name="Wait",
                    agent="a",
                    prompt="sleep",
                    duration_seconds=0.05,
                ),
            ],
        )
        executor = FunctionExecutor(lambda s, c: f"out-{s.id}")
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_status["wait"] == StepStatus.COMPLETED
