# -*- coding: utf-8 -*-
"""TDD tests for SubWorkflowStep (P3-A Task 3).

Covers nested workflow execution: a :class:`SubWorkflowStep` wraps a
full sub-:class:`Workflow` and executes it as a single step in the
parent workflow.  The sub-workflow's result (last step's output)
becomes the parent step's output.
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


def _echo_step(step: WorkflowStep, ctx: dict[str, Any]) -> str:
    """Echo step: returns ``{step_id}:{dep_outputs}``."""
    deps = step.depends_on
    dep_outputs = ",".join(ctx.get(d, "") for d in deps)
    return f"{step.id}:{dep_outputs}"


def _make_sub_workflow(
    wf_id: str = "sub-wf",
    steps: list[WorkflowStep] | None = None,
) -> Workflow:
    """Build a simple sub-workflow."""
    if steps is None:
        steps = [
            WorkflowStep(id="sub-1", name="S1", agent="a", prompt="p"),
            WorkflowStep(
                id="sub-2",
                name="S2",
                agent="a",
                prompt="p",
                depends_on=["sub-1"],
            ),
        ]
    return Workflow(id=wf_id, name=wf_id, steps=steps)


# ── 1. SubWorkflowStep construction ─────────────────────────────


class TestSubWorkflowStepConstruction:
    """SubWorkflowStep — construction and field validation."""

    def test_subworkflow_step_with_sub_workflow(self) -> None:
        """构造: sub_workflow + 标准字段."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        sub_wf = _make_sub_workflow()
        step = SubWorkflowStep(
            id="research",
            name="Research Sub",
            agent="a",
            prompt="research",
            sub_workflow=sub_wf,
        )
        assert step.id == "research"
        assert step.name == "Research Sub"
        assert step.sub_workflow is sub_wf
        assert step.sub_workflow.id == "sub-wf"
        assert len(step.sub_workflow.steps) == 2

    def test_subworkflow_step_inherits_workflow_step_fields(self) -> None:
        """SubWorkflowStep 继承 WorkflowStep 的所有字段."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        step = SubWorkflowStep(
            id="sub",
            name="S",
            agent="a",
            prompt="p",
            sub_workflow=_make_sub_workflow(),
            depends_on=["s1"],
            on_failure="continue",
            max_retries=2,
        )
        assert step.depends_on == ["s1"]
        assert step.on_failure == "continue"
        assert step.max_retries == 2

    def test_subworkflow_step_default_sub_workflow(self) -> None:
        """SubWorkflowStep 默认 sub_workflow 为空 Workflow."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        step = SubWorkflowStep(id="s", name="S", agent="a", prompt="p")
        assert step.sub_workflow.id == ""
        assert step.sub_workflow.steps == []


# ── 2. SubWorkflowStep execution ────────────────────────────────


class TestSubWorkflowStepExecution:
    """SubWorkflowStep — sub-workflow execution."""

    @pytest.mark.asyncio
    async def test_subworkflow_executes_and_returns_last_output(self) -> None:
        """SubWorkflowStep 执行子工作流,返回最后一步的输出."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            return f"out-{step.id}"

        sub_wf = Workflow(
            id="sub",
            name="Sub",
            steps=[
                WorkflowStep(id="a", name="A", agent="x", prompt="p"),
                WorkflowStep(
                    id="b", name="B", agent="x", prompt="p", depends_on=["a"]
                ),
                WorkflowStep(
                    id="c", name="C", agent="x", prompt="p", depends_on=["b"]
                ),
            ],
        )
        wf = Workflow(
            id="parent",
            name="Parent",
            steps=[
                SubWorkflowStep(
                    id="research",
                    name="Research",
                    agent="a",
                    prompt="research",
                    sub_workflow=sub_wf,
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # sub-workflow 的最后一步输出 = "out-c"
        assert result.step_results["research"] == "out-c"

    @pytest.mark.asyncio
    async def test_subworkflow_with_empty_steps_returns_empty(self) -> None:
        """空子工作流(无 steps)返回空字符串."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        empty_sub = Workflow(id="empty", name="Empty", steps=[])
        wf = Workflow(
            id="parent-empty",
            name="Parent Empty",
            steps=[
                SubWorkflowStep(
                    id="empty-sub",
                    name="Empty Sub",
                    agent="a",
                    prompt="p",
                    sub_workflow=empty_sub,
                ),
            ],
        )
        executor = FunctionExecutor(_echo_step)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results.get("empty-sub", "") == ""

    @pytest.mark.asyncio
    async def test_subworkflow_steps_share_context(self) -> None:
        """子工作流的 steps 共享 context(deps 输出可访问)."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "a":
                return "data-from-a"
            if step.id == "b":
                return f"b-got:{ctx.get('a', 'MISSING')}"
            return f"out-{step.id}"

        sub_wf = Workflow(
            id="sub-ctx",
            name="Sub Ctx",
            steps=[
                WorkflowStep(id="a", name="A", agent="x", prompt="p"),
                WorkflowStep(
                    id="b", name="B", agent="x", prompt="p", depends_on=["a"]
                ),
            ],
        )
        wf = Workflow(
            id="parent-ctx",
            name="Parent Ctx",
            steps=[
                SubWorkflowStep(
                    id="sub",
                    name="Sub",
                    agent="a",
                    prompt="p",
                    sub_workflow=sub_wf,
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # b 应该能访问 a 的输出
        assert result.step_results["sub"] == "b-got:data-from-a"

    @pytest.mark.asyncio
    async def test_subworkflow_single_step(self) -> None:
        """单步子工作流."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        sub_wf = Workflow(
            id="single",
            name="Single",
            steps=[
                WorkflowStep(id="only", name="Only", agent="x", prompt="p"),
            ],
        )
        wf = Workflow(
            id="parent-single",
            name="Parent Single",
            steps=[
                SubWorkflowStep(
                    id="sub",
                    name="Sub",
                    agent="a",
                    prompt="p",
                    sub_workflow=sub_wf,
                ),
            ],
        )

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            return f"result-{step.id}"

        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["sub"] == "result-only"


# ── 3. SubWorkflowStep with parent deps ────────────────────────


class TestSubWorkflowStepWithParentDeps:
    """SubWorkflowStep — integration with parent workflow deps."""

    @pytest.mark.asyncio
    async def test_parent_dep_output_available_in_subworkflow(self) -> None:
        """父工作流 dep 的输出可以在子工作流中访问."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "seed":
                return "seed-value"
            if step.id == "use-seed":
                return f"got:{ctx.get('seed', 'MISSING')}"
            return f"out-{step.id}"

        sub_wf = Workflow(
            id="sub",
            name="Sub",
            steps=[
                WorkflowStep(
                    id="use-seed", name="UseSeed", agent="x", prompt="p"
                ),
            ],
        )
        wf = Workflow(
            id="parent-dep",
            name="Parent Dep",
            steps=[
                WorkflowStep(id="seed", name="Seed", agent="a", prompt="p"),
                SubWorkflowStep(
                    id="sub",
                    name="Sub",
                    agent="a",
                    prompt="p",
                    sub_workflow=sub_wf,
                    depends_on=["seed"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # sub-workflow 的 use-seed 应该能访问父的 seed 输出
        assert result.step_results["sub"] == "got:seed-value"

    @pytest.mark.asyncio
    async def test_step_after_subworkflow_uses_its_output(self) -> None:
        """subworkflow 之后的 step 可以使用 subworkflow 的输出."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "sub-a":
                return "sub-result"
            if step.id == "final":
                return f"final:{ctx.get('research', 'MISSING')}"
            return f"out-{step.id}"

        sub_wf = Workflow(
            id="sub",
            name="Sub",
            steps=[
                WorkflowStep(id="sub-a", name="SubA", agent="x", prompt="p"),
            ],
        )
        wf = Workflow(
            id="parent-after",
            name="Parent After",
            steps=[
                SubWorkflowStep(
                    id="research",
                    name="Research",
                    agent="a",
                    prompt="p",
                    sub_workflow=sub_wf,
                ),
                WorkflowStep(
                    id="final",
                    name="Final",
                    agent="a",
                    prompt="p",
                    depends_on=["research"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["final"] == "final:sub-result"


# ── 4. SubWorkflowStep failure propagation ─────────────────────


class TestSubWorkflowStepFailure:
    """SubWorkflowStep — failure propagation to parent."""

    @pytest.mark.asyncio
    async def test_subworkflow_failure_aborts_parent(self) -> None:
        """子工作流 step 失败(on_failure=abort)→ 父 step 失败."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "fail-step":
                raise RuntimeError("sub failed")
            return f"out-{step.id}"

        sub_wf = Workflow(
            id="sub-fail",
            name="Sub Fail",
            steps=[
                WorkflowStep(
                    id="fail-step",
                    name="Fail",
                    agent="x",
                    prompt="p",
                    on_failure="abort",
                ),
            ],
        )
        wf = Workflow(
            id="parent-fail",
            name="Parent Fail",
            steps=[
                SubWorkflowStep(
                    id="sub",
                    name="Sub",
                    agent="a",
                    prompt="p",
                    sub_workflow=sub_wf,
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        # 子工作流失败 → 父 step 失败 → workflow 失败
        assert result.status == WorkflowStatus.FAILED

    @pytest.mark.asyncio
    async def test_subworkflow_continue_on_failure(self) -> None:
        """子工作流 on_failure=continue 时不终止父 workflow."""
        from xruntime._runtime._workflow._steps import SubWorkflowStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "fail-step":
                raise RuntimeError("sub failed")
            return f"out-{step.id}"

        sub_wf = Workflow(
            id="sub-cont",
            name="Sub Cont",
            steps=[
                WorkflowStep(
                    id="fail-step",
                    name="Fail",
                    agent="x",
                    prompt="p",
                    on_failure="continue",
                ),
                WorkflowStep(
                    id="after-fail",
                    name="After",
                    agent="x",
                    prompt="p",
                    depends_on=["fail-step"],
                ),
            ],
        )
        wf = Workflow(
            id="parent-cont",
            name="Parent Cont",
            steps=[
                SubWorkflowStep(
                    id="sub",
                    name="Sub",
                    agent="a",
                    prompt="p",
                    sub_workflow=sub_wf,
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        # continue → workflow 不失败
        assert result.status == WorkflowStatus.COMPLETED
