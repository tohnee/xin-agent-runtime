# -*- coding: utf-8 -*-
"""TDD tests for ConditionalStep (P3-A Task 1).

Covers runtime conditional branching: a ``ConditionalStep`` wraps a
list of inner steps and a ``condition`` predicate.  When the
predicate evaluates to ``True`` on the workflow context, the inner
steps are executed; when ``False``, they are all marked
``SKIPPED`` and the branch produces an empty output.

Multiple branches can be declared on a workflow — the first branch
whose condition matches is executed; subsequent branches are skipped
(mutual exclusion at the branch level).
"""
from __future__ import annotations

from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    Orchestrator,
    StepStatus,
    Workflow,
    WorkflowResult,
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


def _classify_step(step: WorkflowStep, ctx: dict[str, Any]) -> str:
    """Classify based on input context."""
    text = ctx.get("input", "")
    if "urgent" in text.lower():
        return "urgent"
    if "normal" in text.lower():
        return "normal"
    return "unknown"


# ── 1. ConditionalStep construction ─────────────────────────────


class TestConditionalStepConstruction:
    """ConditionalStep — construction and field validation."""

    def test_conditional_step_with_condition_and_inner_steps(self) -> None:
        """构造: condition + inner_steps + 标准字段."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        step = ConditionalStep(
            id="branch-urgent",
            name="Urgent Branch",
            agent="coder",
            prompt="escalate",
            condition=lambda ctx: ctx.get("classify") == "urgent",
            inner_steps=[
                WorkflowStep(
                    id="escalate",
                    name="Escalate",
                    agent="a",
                    prompt="escalate",
                ),
            ],
        )
        assert step.id == "branch-urgent"
        assert step.name == "Urgent Branch"
        assert step.agent == "coder"
        assert step.prompt == "escalate"
        assert step.condition is not None
        assert len(step.inner_steps) == 1
        assert step.inner_steps[0].id == "escalate"

    def test_conditional_step_inherits_workflow_step_fields(self) -> None:
        """ConditionalStep 继承 WorkflowStep 的所有字段."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        step = ConditionalStep(
            id="b1",
            name="Branch",
            agent="a",
            prompt="p",
            condition=lambda ctx: True,
            inner_steps=[],
            depends_on=["s1"],
            on_failure="retry",
            max_retries=3,
        )
        assert step.depends_on == ["s1"]
        assert step.on_failure == "retry"
        assert step.max_retries == 3

    def test_conditional_step_inner_steps_defaults_to_empty(self) -> None:
        """inner_steps 默认为空列表."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        step = ConditionalStep(
            id="b1",
            name="B",
            agent="a",
            prompt="p",
            condition=lambda ctx: True,
        )
        assert step.inner_steps == []

    def test_conditional_step_eval_condition(self) -> None:
        """eval_condition 在给定 context 上执行 predicate."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        step = ConditionalStep(
            id="b1",
            name="B",
            agent="a",
            prompt="p",
            condition=lambda ctx: ctx.get("x") == 1,
        )
        assert step.eval_condition({"x": 1}) is True
        assert step.eval_condition({"x": 2}) is False
        assert step.eval_condition({}) is False

    def test_conditional_step_eval_condition_handles_exception(self) -> None:
        """eval_condition 在 predicate 抛异常时返回 False(fail-closed)."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        def bad_condition(ctx: dict[str, Any]) -> bool:
            raise RuntimeError("boom")

        step = ConditionalStep(
            id="b1",
            name="B",
            agent="a",
            prompt="p",
            condition=bad_condition,
        )
        # 异常应被捕获,返回 False(fail-closed,不执行分支)
        assert step.eval_condition({"x": 1}) is False


# ── 2. ConditionalStep execution ────────────────────────────────


class TestConditionalStepExecution:
    """ConditionalStep — execution with condition True/False."""

    @pytest.mark.asyncio
    async def test_condition_true_executes_inner_steps(self) -> None:
        """condition=True 时执行 inner_steps,branch 输出 = 最后一个
        inner step 的输出."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        # 构造 workflow:
        #   s1 (input) → branch (condition=True, inner: b1, b2)
        wf = Workflow(
            id="wf-cond-true",
            name="Conditional True",
            steps=[
                WorkflowStep(id="s1", name="S1", agent="a", prompt="p"),
                ConditionalStep(
                    id="branch",
                    name="Branch",
                    agent="a",
                    prompt="branch",
                    condition=lambda ctx: True,
                    inner_steps=[
                        WorkflowStep(
                            id="b1", name="B1", agent="a", prompt="p"
                        ),
                        WorkflowStep(
                            id="b2", name="B2", agent="a", prompt="p"
                        ),
                    ],
                    depends_on=["s1"],
                ),
            ],
        )
        executor = FunctionExecutor(_echo_step)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # branch 应有输出(inner steps 执行后产生)
        assert "branch" in result.step_results
        # inner steps 不在顶层 step_results(它们是分支内部)
        assert "b1" not in result.step_results
        assert "b2" not in result.step_results

    @pytest.mark.asyncio
    async def test_condition_false_skips_inner_steps(self) -> None:
        """condition=False 时跳过 inner_steps,branch 输出为空字符串."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        wf = Workflow(
            id="wf-cond-false",
            name="Conditional False",
            steps=[
                WorkflowStep(id="s1", name="S1", agent="a", prompt="p"),
                ConditionalStep(
                    id="branch",
                    name="Branch",
                    agent="a",
                    prompt="branch",
                    condition=lambda ctx: False,
                    inner_steps=[
                        WorkflowStep(
                            id="b1", name="B1", agent="a", prompt="p"
                        ),
                    ],
                    depends_on=["s1"],
                ),
            ],
        )
        executor = FunctionExecutor(_echo_step)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # branch 被执行(作为 step),但 condition=False → 输出空字符串
        assert result.step_status["branch"] == StepStatus.COMPLETED
        assert result.step_results.get("branch", "") == ""

    @pytest.mark.asyncio
    async def test_condition_uses_context_from_dependencies(self) -> None:
        """condition 使用依赖 step 的输出作为上下文."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        # s1 返回 "urgent",branch 的 condition 检查 ctx["s1"]=="urgent"
        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "s1":
                return "urgent"
            return f"{step.id}"

        wf = Workflow(
            id="wf-cond-ctx",
            name="Conditional Context",
            steps=[
                WorkflowStep(id="s1", name="S1", agent="a", prompt="p"),
                ConditionalStep(
                    id="branch",
                    name="Branch",
                    agent="a",
                    prompt="branch",
                    condition=lambda ctx: ctx.get("s1") == "urgent",
                    inner_steps=[
                        WorkflowStep(
                            id="b1", name="B1", agent="a", prompt="p"
                        ),
                    ],
                    depends_on=["s1"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # s1 输出 "urgent" → condition=True → branch 执行 inner
        assert "branch" in result.step_results
        assert result.step_results["branch"] != ""

    @pytest.mark.asyncio
    async def test_branch_output_is_last_inner_step_output(self) -> None:
        """branch 的输出 = 最后一个 inner step 的输出."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            return f"out-{step.id}"

        wf = Workflow(
            id="wf-branch-out",
            name="Branch Output",
            steps=[
                ConditionalStep(
                    id="branch",
                    name="Branch",
                    agent="a",
                    prompt="branch",
                    condition=lambda ctx: True,
                    inner_steps=[
                        WorkflowStep(
                            id="b1", name="B1", agent="a", prompt="p"
                        ),
                        WorkflowStep(
                            id="b2", name="B2", agent="a", prompt="p"
                        ),
                        WorkflowStep(
                            id="b3", name="B3", agent="a", prompt="p"
                        ),
                    ],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # branch 输出 = b3 的输出 = "out-b3"
        assert result.step_results["branch"] == "out-b3"


# ── 3. Multiple branches (mutual exclusion) ────────────────────


class TestMultipleBranchesMutualExclusion:
    """Multiple ConditionalStep branches — first match wins."""

    @pytest.mark.asyncio
    async def test_first_matching_branch_executes(self) -> None:
        """多个 branch 时,第一个 condition=True 的执行,其余跳过."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "classify":
                return "urgent"
            return f"out-{step.id}"

        wf = Workflow(
            id="wf-multi-branch",
            name="Multi Branch",
            steps=[
                WorkflowStep(
                    id="classify", name="Classify", agent="a", prompt="p"
                ),
                ConditionalStep(
                    id="branch-urgent",
                    name="Urgent",
                    agent="a",
                    prompt="p",
                    condition=lambda ctx: ctx.get("classify") == "urgent",
                    inner_steps=[
                        WorkflowStep(
                            id="escalate", name="Esc", agent="a", prompt="p"
                        ),
                    ],
                    depends_on=["classify"],
                ),
                ConditionalStep(
                    id="branch-normal",
                    name="Normal",
                    agent="a",
                    prompt="p",
                    condition=lambda ctx: ctx.get("classify") == "normal",
                    inner_steps=[
                        WorkflowStep(
                            id="queue", name="Q", agent="a", prompt="p"
                        ),
                    ],
                    depends_on=["classify"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # branch-urgent 执行了(classify=urgent)
        assert result.step_results.get("branch-urgent", "") != ""
        # branch-normal 跳过(condition=False)
        assert result.step_results.get("branch-normal", "") == ""

    @pytest.mark.asyncio
    async def test_no_branch_matches_all_skipped(self) -> None:
        """所有 branch 的 condition 都为 False 时,全部跳过."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "classify":
                return "unknown"
            return f"out-{step.id}"

        wf = Workflow(
            id="wf-no-match",
            name="No Match",
            steps=[
                WorkflowStep(id="classify", name="C", agent="a", prompt="p"),
                ConditionalStep(
                    id="b1",
                    name="B1",
                    agent="a",
                    prompt="p",
                    condition=lambda ctx: ctx.get("classify") == "urgent",
                    inner_steps=[
                        WorkflowStep(
                            id="x1", name="X1", agent="a", prompt="p"
                        ),
                    ],
                    depends_on=["classify"],
                ),
                ConditionalStep(
                    id="b2",
                    name="B2",
                    agent="a",
                    prompt="p",
                    condition=lambda ctx: ctx.get("classify") == "normal",
                    inner_steps=[
                        WorkflowStep(
                            id="x2", name="X2", agent="a", prompt="p"
                        ),
                    ],
                    depends_on=["classify"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # 两个 branch 都跳过
        assert result.step_results.get("b1", "") == ""
        assert result.step_results.get("b2", "") == ""


# ── 4. Branch followed by dependent steps ──────────────────────


class TestBranchWithDependentSteps:
    """ConditionalStep followed by steps that depend on the branch."""

    @pytest.mark.asyncio
    async def test_step_after_branch_uses_branch_output(self) -> None:
        """branch 之后的 step 可以使用 branch 的输出."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "b1":
                return "branch-result"
            if step.id == "final":
                return f"final:{ctx.get('branch', '')}"
            return f"out-{step.id}"

        wf = Workflow(
            id="wf-branch-dep",
            name="Branch Dep",
            steps=[
                ConditionalStep(
                    id="branch",
                    name="Branch",
                    agent="a",
                    prompt="p",
                    condition=lambda ctx: True,
                    inner_steps=[
                        WorkflowStep(
                            id="b1", name="B1", agent="a", prompt="p"
                        ),
                    ],
                ),
                WorkflowStep(
                    id="final",
                    name="Final",
                    agent="a",
                    prompt="p",
                    depends_on=["branch"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # final 使用 branch 的输出(branch-result)
        assert result.step_results["final"] == "final:branch-result"

    @pytest.mark.asyncio
    async def test_step_after_skipped_branch_gets_empty(self) -> None:
        """branch 跳过时(condition=False),依赖它的 step 收到空输出."""
        from xruntime._runtime._workflow._steps import ConditionalStep

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "final":
                return f"final:{ctx.get('branch', '')}"
            return f"out-{step.id}"

        wf = Workflow(
            id="wf-skipped-branch-dep",
            name="Skipped Branch Dep",
            steps=[
                ConditionalStep(
                    id="branch",
                    name="Branch",
                    agent="a",
                    prompt="p",
                    condition=lambda ctx: False,
                    inner_steps=[
                        WorkflowStep(
                            id="b1", name="B1", agent="a", prompt="p"
                        ),
                    ],
                ),
                WorkflowStep(
                    id="final",
                    name="Final",
                    agent="a",
                    prompt="p",
                    depends_on=["branch"],
                ),
            ],
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # branch 跳过 → 输出空字符串 → final 使用空输出
        assert result.step_results["final"] == "final:"
