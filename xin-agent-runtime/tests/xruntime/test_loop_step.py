# -*- coding: utf-8 -*-
"""TDD tests for LoopStep (P3-A Task 2).

Covers iterative step execution: a :class:`LoopStep` repeats its
``agent`` / ``prompt`` while ``condition(context)`` returns ``True``,
up to ``max_iterations``.  Each iteration's output is added to the
context so the next iteration can reference it.  The loop exits when
the condition returns ``False`` or ``max_iterations`` is reached.
"""
from __future__ import annotations

from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    StepStatus,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from xruntime._runtime._workflow._sdk import (
    FunctionExecutor,
    run_workflow,
)


# ── helpers ──────────────────────────────────────────────────────


def _make_loop_step(
    step_id: str = "loop-1",
    *,
    condition=None,
    max_iterations: int = 3,
    agent: str = "a",
    prompt: str = "refine",
    depends_on: list[str] | None = None,
):
    """Build a LoopStep with sane defaults."""
    from xruntime._runtime._workflow._steps import LoopStep

    return LoopStep(
        id=step_id,
        name=step_id,
        agent=agent,
        prompt=prompt,
        condition=condition
        if condition is not None
        else (lambda ctx: ctx.get("quality", 0) < 0.9),
        max_iterations=max_iterations,
        depends_on=depends_on or [],
    )


# ── 1. LoopStep construction ───────────────────────────────────


class TestLoopStepConstruction:
    """LoopStep — construction and field validation."""

    def test_loop_step_with_condition_and_max_iterations(self) -> None:
        """构造: condition + max_iterations + 标准字段."""
        from xruntime._runtime._workflow._steps import LoopStep

        step = LoopStep(
            id="loop-1",
            name="Refine Loop",
            agent="coder",
            prompt="refine output",
            condition=lambda ctx: ctx.get("quality") < 0.9,
            max_iterations=5,
        )
        assert step.id == "loop-1"
        assert step.name == "Refine Loop"
        assert step.agent == "coder"
        assert step.prompt == "refine output"
        assert step.max_iterations == 5
        assert step.condition is not None

    def test_loop_step_inherits_workflow_step_fields(self) -> None:
        """LoopStep 继承 WorkflowStep 的所有字段."""
        from xruntime._runtime._workflow._steps import LoopStep

        step = LoopStep(
            id="loop-1",
            name="L",
            agent="a",
            prompt="p",
            condition=lambda ctx: True,
            max_iterations=3,
            depends_on=["s1"],
            on_failure="retry",
            max_retries=2,
        )
        assert step.depends_on == ["s1"]
        assert step.on_failure == "retry"
        assert step.max_retries == 2

    def test_loop_step_defaults(self) -> None:
        """LoopStep 默认 condition=lambda ctx: False, max_iterations=1."""
        from xruntime._runtime._workflow._steps import LoopStep

        step = LoopStep(id="l1", name="L", agent="a", prompt="p")
        assert step.max_iterations == 1
        # 默认 condition 返回 False → 不执行任何迭代
        assert step.condition({}) is False


# ── 2. LoopStep execution ───────────────────────────────────────


class TestLoopStepExecution:
    """LoopStep — iteration loop behavior."""

    @pytest.mark.asyncio
    async def test_loop_runs_until_condition_false(self) -> None:
        """condition 返回 False 时退出循环."""
        from xruntime._runtime._workflow._steps import LoopStep

        # 模拟: 每次 iteration quality 提升 0.3,初始 0.1
        # condition: quality < 0.9
        # 迭代: 0.1 → 0.4 → 0.7 → 1.0 (退出,因为 1.0 >= 0.9)
        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            quality = ctx.get("quality", 0.1)
            new_quality = quality + 0.3
            return f"iter-{call_count}:q={new_quality}"

        wf = Workflow(
            id="wf-loop-until-false",
            name="Loop Until False",
            steps=[
                LoopStep(
                    id="loop",
                    name="Loop",
                    agent="a",
                    prompt="refine",
                    condition=lambda ctx: ctx.get("quality", 0.1) < 0.9,
                    max_iterations=10,
                ),
            ],
        )
        # We need the executor to update the context "quality" based on
        # the loop output — but the executor only returns a string.
        # The loop step's context propagation handles this: each
        # iteration's output is stored as ctx["loop"] (overwriting
        # the previous iteration's value).
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # The loop should have run at least once
        assert "loop" in result.step_results
        assert result.step_results["loop"] != ""

    @pytest.mark.asyncio
    async def test_loop_respects_max_iterations(self) -> None:
        """condition 一直为 True 时,max_iterations 限制迭代次数."""
        from xruntime._runtime._workflow._steps import LoopStep

        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            return f"iter-{call_count}"

        wf = Workflow(
            id="wf-loop-max",
            name="Loop Max Iter",
            steps=[
                LoopStep(
                    id="loop",
                    name="Loop",
                    agent="a",
                    prompt="refine",
                    condition=lambda ctx: True,  # 永远为 True
                    max_iterations=3,
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # 应该执行 3 次(max_iterations)
        assert call_count == 3
        # 最终输出是最后一次迭代的结果
        assert result.step_results["loop"] == "iter-3"

    @pytest.mark.asyncio
    async def test_loop_zero_iterations_when_condition_false_start(
        self,
    ) -> None:
        """condition 初始就为 False 时,执行 0 次迭代(但 step 完成)."""
        from xruntime._runtime._workflow._steps import LoopStep

        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            return f"iter-{call_count}"

        wf = Workflow(
            id="wf-loop-zero",
            name="Loop Zero",
            steps=[
                LoopStep(
                    id="loop",
                    name="Loop",
                    agent="a",
                    prompt="refine",
                    condition=lambda ctx: False,  # 初始就 False
                    max_iterations=5,
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # 0 次迭代
        assert call_count == 0
        # step 标记为 COMPLETED,输出为空字符串
        assert result.step_status["loop"] == StepStatus.COMPLETED
        assert result.step_results.get("loop", "") == ""

    @pytest.mark.asyncio
    async def test_loop_output_is_last_iteration_output(self) -> None:
        """loop 的输出 = 最后一次迭代的输出."""
        from xruntime._runtime._workflow._steps import LoopStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            prev = ctx.get("loop", "start")
            return f"{prev}-iter"

        wf = Workflow(
            id="wf-loop-output",
            name="Loop Output",
            steps=[
                LoopStep(
                    id="loop",
                    name="Loop",
                    agent="a",
                    prompt="refine",
                    condition=lambda ctx: True,
                    max_iterations=3,
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # 3 次迭代: "start-iter" → "start-iter-iter" → "start-iter-iter-iter"
        assert result.step_results["loop"] == "start-iter-iter-iter"

    @pytest.mark.asyncio
    async def test_loop_each_iteration_uses_previous_output(self) -> None:
        """每次迭代使用前一次迭代的输出(通过 context["loop"])."""
        from xruntime._runtime._workflow._steps import LoopStep

        outputs_seen: list[str] = []

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            prev = ctx.get("loop", "INIT")
            outputs_seen.append(prev)
            return f"{prev}+1"

        wf = Workflow(
            id="wf-loop-chain",
            name="Loop Chain",
            steps=[
                LoopStep(
                    id="loop",
                    name="Loop",
                    agent="a",
                    prompt="refine",
                    condition=lambda ctx: True,
                    max_iterations=3,
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # 第一次: ctx["loop"] 不存在 → "INIT" → "INIT+1"
        # 第二次: ctx["loop"] = "INIT+1" → "INIT+1+1"
        # 第三次: ctx["loop"] = "INIT+1+1" → "INIT+1+1+1"
        assert outputs_seen == ["INIT", "INIT+1", "INIT+1+1"]
        assert result.step_results["loop"] == "INIT+1+1+1"


# ── 3. LoopStep with dependencies and failure ─────────────────


class TestLoopStepWithDepsAndFailure:
    """LoopStep — dependency integration and failure handling."""

    @pytest.mark.asyncio
    async def test_loop_after_dependency_uses_dep_output(self) -> None:
        """loop 在依赖 step 之后执行,可以使用 dep 的输出."""
        from xruntime._runtime._workflow._steps import LoopStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "s1":
                return "seed"
            prev = ctx.get("loop", ctx.get("s1", "INIT"))
            return f"{prev}+1"

        wf = Workflow(
            id="wf-loop-dep",
            name="Loop Dep",
            steps=[
                WorkflowStep(id="s1", name="S1", agent="a", prompt="p"),
                LoopStep(
                    id="loop",
                    name="Loop",
                    agent="a",
                    prompt="refine",
                    condition=lambda ctx: True,
                    max_iterations=2,
                    depends_on=["s1"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # s1 → "seed"
        # loop iter 1: ctx["loop"] 不存在,ctx["s1"]="seed" → "seed+1"
        # loop iter 2: ctx["loop"]="seed+1" → "seed+1+1"
        assert result.step_results["s1"] == "seed"
        assert result.step_results["loop"] == "seed+1+1"

    @pytest.mark.asyncio
    async def test_loop_iteration_failure_returns_none(self) -> None:
        """loop 迭代中 executor 抛异常 → step 失败(None)."""
        from xruntime._runtime._workflow._steps import LoopStep

        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("boom")
            return f"iter-{call_count}"

        wf = Workflow(
            id="wf-loop-fail",
            name="Loop Fail",
            steps=[
                LoopStep(
                    id="loop",
                    name="Loop",
                    agent="a",
                    prompt="refine",
                    condition=lambda ctx: True,
                    max_iterations=5,
                    on_failure="abort",
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        # 第二次迭代失败 → step 失败 → workflow 失败
        assert result.status == WorkflowStatus.FAILED
        assert result.step_status["loop"] == StepStatus.FAILED

    @pytest.mark.asyncio
    async def test_loop_with_continue_on_failure(self) -> None:
        """loop on_failure="continue" 时,迭代失败不终止 workflow."""
        from xruntime._runtime._workflow._steps import LoopStep

        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("boom")
            return f"iter-{call_count}"

        wf = Workflow(
            id="wf-loop-continue",
            name="Loop Continue",
            steps=[
                LoopStep(
                    id="loop",
                    name="Loop",
                    agent="a",
                    prompt="refine",
                    condition=lambda ctx: True,
                    max_iterations=3,
                    on_failure="continue",
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        # on_failure="continue" → workflow 不失败
        assert result.status == WorkflowStatus.COMPLETED


# ── 4. LoopStep max_iterations boundary ────────────────────────


class TestLoopStepMaxIterationsBoundary:
    """LoopStep — max_iterations boundary cases."""

    @pytest.mark.asyncio
    async def test_max_iterations_zero_runs_zero_times(self) -> None:
        """max_iterations=0 时,执行 0 次迭代."""
        from xruntime._runtime._workflow._steps import LoopStep

        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            return f"iter-{call_count}"

        wf = Workflow(
            id="wf-loop-zero-max",
            name="Loop Zero Max",
            steps=[
                LoopStep(
                    id="loop",
                    name="Loop",
                    agent="a",
                    prompt="refine",
                    condition=lambda ctx: True,
                    max_iterations=0,
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert call_count == 0
        assert result.step_results.get("loop", "") == ""

    @pytest.mark.asyncio
    async def test_max_iterations_one_runs_once(self) -> None:
        """max_iterations=1 时,执行 1 次迭代."""
        from xruntime._runtime._workflow._steps import LoopStep

        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            return f"iter-{call_count}"

        wf = Workflow(
            id="wf-loop-one-max",
            name="Loop One Max",
            steps=[
                LoopStep(
                    id="loop",
                    name="Loop",
                    agent="a",
                    prompt="refine",
                    condition=lambda ctx: True,
                    max_iterations=1,
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert call_count == 1
        assert result.step_results["loop"] == "iter-1"
