# -*- coding: utf-8 -*-
"""TDD tests for P3-A Task 5: SDK builder methods + integration.

Two test groups:

1. **Builder methods** — :meth:`WorkflowBuilder.branch` /
   :meth:`WorkflowBuilder.loop` / :meth:`WorkflowBuilder.subworkflow` /
   :meth:`WorkflowBuilder.sleep` produce the correct step types.

2. **Cross-type integration** — workflows combining multiple new
   step types (branch + loop, subworkflow + timer, etc.) execute
   correctly end-to-end.
"""
from __future__ import annotations

from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from xruntime._runtime._workflow._sdk import (
    FunctionExecutor,
    WorkflowBuilder,
    run_workflow,
)


# ── helpers ──────────────────────────────────────────────────────


def _echo_step(step: WorkflowStep, ctx: dict[str, Any]) -> str:
    """Echo step: returns ``{step_id}:{dep_outputs}``."""
    deps = step.depends_on
    dep_outputs = ",".join(ctx.get(d, "") for d in deps)
    return f"{step.id}:{dep_outputs}"


# ── 1. Builder methods produce correct step types ──────────────


class TestWorkflowBuilderBranch:
    """WorkflowBuilder.branch() — produces ConditionalStep."""

    def test_branch_adds_conditional_step(self) -> None:
        """.branch() 添加 ConditionalStep 到 steps."""
        wf = (
            WorkflowBuilder()
            .id("wf-b")
            .branch(
                id="b1",
                agent="a",
                condition=lambda ctx: True,
                inner_steps=[
                    WorkflowStep(id="x", name="X", agent="a", prompt="p"),
                ],
            )
            .build()
        )
        from xruntime._runtime._workflow._steps import ConditionalStep

        assert len(wf.steps) == 1
        assert isinstance(wf.steps[0], ConditionalStep)
        assert wf.steps[0].id == "b1"
        assert len(wf.steps[0].inner_steps) == 1

    def test_branch_default_condition_is_true(self) -> None:
        """不传 condition 时默认为 always-True."""
        wf = WorkflowBuilder().id("wf-bd").branch(id="b1", agent="a").build()
        assert wf.steps[0].condition({}) is True

    def test_branch_with_depends_on(self) -> None:
        """.branch() 支持 depends_on."""
        wf = (
            WorkflowBuilder()
            .id("wf-bd2")
            .step(id="s1", agent="a", prompt="p")
            .branch(
                id="b1",
                agent="a",
                depends_on=["s1"],
                condition=lambda ctx: True,
            )
            .build()
        )
        assert wf.steps[1].depends_on == ["s1"]


class TestWorkflowBuilderLoop:
    """WorkflowBuilder.loop() — produces LoopStep."""

    def test_loop_adds_loop_step(self) -> None:
        """.loop() 添加 LoopStep 到 steps."""
        wf = (
            WorkflowBuilder()
            .id("wf-l")
            .loop(
                id="loop-1",
                agent="a",
                prompt="refine",
                condition=lambda ctx: True,
                max_iterations=5,
            )
            .build()
        )
        from xruntime._runtime._workflow._steps import LoopStep

        assert len(wf.steps) == 1
        assert isinstance(wf.steps[0], LoopStep)
        assert wf.steps[0].max_iterations == 5

    def test_loop_default_condition_is_false(self) -> None:
        """不传 condition 时默认为 always-False(零迭代)."""
        wf = (
            WorkflowBuilder()
            .id("wf-ld")
            .loop(id="l1", agent="a", prompt="p")
            .build()
        )
        assert wf.steps[0].condition({}) is False


class TestWorkflowBuilderSubworkflow:
    """WorkflowBuilder.subworkflow() — produces SubWorkflowStep."""

    def test_subworkflow_adds_subworkflow_step(self) -> None:
        """.subworkflow() 添加 SubWorkflowStep."""
        sub_wf = Workflow(
            id="sub",
            name="Sub",
            steps=[WorkflowStep(id="a", name="A", agent="x", prompt="p")],
        )
        wf = (
            WorkflowBuilder()
            .id("wf-sw")
            .subworkflow(id="sub-step", workflow=sub_wf)
            .build()
        )
        from xruntime._runtime._workflow._steps import (
            SubWorkflowStep,
        )

        assert len(wf.steps) == 1
        assert isinstance(wf.steps[0], SubWorkflowStep)
        assert wf.steps[0].sub_workflow.id == "sub"


class TestWorkflowBuilderSleep:
    """WorkflowBuilder.sleep() — produces TimerStep."""

    def test_sleep_adds_timer_step(self) -> None:
        """.sleep() 添加 TimerStep."""
        wf = (
            WorkflowBuilder()
            .id("wf-s")
            .sleep(id="wait", duration_seconds=60)
            .build()
        )
        from xruntime._runtime._workflow._steps import TimerStep

        assert len(wf.steps) == 1
        assert isinstance(wf.steps[0], TimerStep)
        assert wf.steps[0].duration_seconds == 60


# ── 2. Cross-type integration ───────────────────────────────────


class TestBranchAndLoopIntegration:
    """ConditionalStep + LoopStep combined."""

    @pytest.mark.asyncio
    async def test_branch_then_loop(self) -> None:
        """branch(condition=True) → loop 执行."""
        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            return f"out-{step.id}-{call_count}"

        wf = (
            WorkflowBuilder()
            .id("wf-branch-loop")
            .step(id="s1", agent="a", prompt="p")
            .branch(
                id="branch",
                agent="a",
                condition=lambda ctx: ctx.get("s1", "").startswith("out"),
                inner_steps=[
                    WorkflowStep(id="b1", name="B1", agent="a", prompt="p"),
                ],
                depends_on=["s1"],
            )
            .loop(
                id="loop",
                agent="a",
                prompt="refine",
                condition=lambda ctx: True,
                max_iterations=2,
                depends_on=["branch"],
            )
            .build()
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # loop 应该执行 2 次
        assert "loop" in result.step_results


class TestSubworkflowAndTimerIntegration:
    """SubWorkflowStep + TimerStep combined."""

    @pytest.mark.asyncio
    async def test_subworkflow_then_timer(self) -> None:
        """subworkflow → timer(0) → final step."""
        sub_wf = Workflow(
            id="sub",
            name="Sub",
            steps=[
                WorkflowStep(id="sub-a", name="SA", agent="x", prompt="p"),
            ],
        )

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "sub-a":
                return "sub-result"
            if step.id == "final":
                return f"final:{ctx.get('sub', '')}"
            return f"out-{step.id}"

        wf = (
            WorkflowBuilder()
            .id("wf-sub-timer")
            .subworkflow(id="sub", workflow=sub_wf)
            .sleep(id="wait", duration_seconds=0, depends_on=["sub"])
            .step(id="final", agent="a", prompt="p", depends_on=["wait"])
            .build()
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["sub"] == "sub-result"
        assert result.step_results["wait"] == ""
        assert result.step_results["final"] == "final:sub-result"


class TestFullStackControlFlow:
    """Full stack: all 4 new step types in one workflow."""

    @pytest.mark.asyncio
    async def test_all_four_step_types_in_one_workflow(self) -> None:
        """一个 workflow 同时使用 branch + loop + subworkflow + timer."""
        sub_wf = Workflow(
            id="sub",
            name="Sub",
            steps=[
                WorkflowStep(id="sub-1", name="S1", agent="x", prompt="p"),
            ],
        )

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            return f"out-{step.id}"

        wf = (
            WorkflowBuilder()
            .id("wf-full")
            .step(id="start", agent="a", prompt="p")
            .branch(
                id="branch",
                agent="a",
                condition=lambda ctx: True,
                inner_steps=[
                    WorkflowStep(id="b1", name="B1", agent="a", prompt="p"),
                ],
                depends_on=["start"],
            )
            .loop(
                id="loop",
                agent="a",
                prompt="refine",
                condition=lambda ctx: True,
                max_iterations=2,
                depends_on=["branch"],
            )
            .subworkflow(
                id="sub",
                workflow=sub_wf,
                depends_on=["loop"],
            )
            .sleep(
                id="wait",
                duration_seconds=0,
                depends_on=["sub"],
            )
            .step(id="end", agent="a", prompt="p", depends_on=["wait"])
            .build()
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # 所有 step 都应完成
        for step_id in ["start", "branch", "loop", "sub", "wait", "end"]:
            assert step_id in result.step_results


class TestBackwardCompatibility:
    """Old workflows without new step types still work."""

    @pytest.mark.asyncio
    async def test_plain_workflow_still_works(self) -> None:
        """不使用新 step 类型的 workflow 仍然正常工作."""
        wf = (
            WorkflowBuilder()
            .id("wf-plain")
            .step(id="s1", agent="a", prompt="p")
            .step(id="s2", agent="a", prompt="p", depends_on=["s1"])
            .build()
        )
        executor = FunctionExecutor(_echo_step)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert "s1" in result.step_results
        assert "s2" in result.step_results

    @pytest.mark.asyncio
    async def test_mixed_old_and_new_steps(self) -> None:
        """混合使用旧 step 和新 step 类型."""
        sub_wf = Workflow(
            id="sub",
            name="Sub",
            steps=[WorkflowStep(id="x", name="X", agent="a", prompt="p")],
        )
        wf = (
            WorkflowBuilder()
            .id("wf-mixed")
            .step(id="s1", agent="a", prompt="p")
            .branch(
                id="b1",
                agent="a",
                condition=lambda ctx: True,
                inner_steps=[
                    WorkflowStep(
                        id="b-inner", name="BI", agent="a", prompt="p"
                    ),
                ],
                depends_on=["s1"],
            )
            .step(id="s2", agent="a", prompt="p", depends_on=["b1"])
            .build()
        )
        executor = FunctionExecutor(_echo_step)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
