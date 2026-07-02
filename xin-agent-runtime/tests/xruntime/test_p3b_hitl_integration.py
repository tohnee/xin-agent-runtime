# -*- coding: utf-8 -*-
"""TDD tests for P3-B Task 3: HITL integration.

End-to-end tests for ApprovalStep execution in workflows:

1. ApprovalStep in a workflow creates a request in the store.
2. Auto-approve when no store is configured (dev/test mode).
3. WorkflowBuilder.approval() produces ApprovalStep.
4. ApprovalStep combined with other step types.
5. Timeout policy behavior.
"""
from __future__ import annotations

from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    Orchestrator,
    StepStatus,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from xruntime._runtime._workflow._approval import (
    ApprovalStep,
    InMemoryApprovalStore,
)
from xruntime._runtime._workflow._sdk import (
    FunctionExecutor,
    WorkflowBuilder,
    run_workflow,
)


# ── helpers ──────────────────────────────────────────────────────


def _echo_step(step: WorkflowStep, ctx: dict[str, Any]) -> str:
    return f"out-{step.id}"


# ── 1. ApprovalStep execution ─────────────────────────────────


class TestApprovalStepExecution:
    """ApprovalStep — execution in a workflow."""

    @pytest.mark.asyncio
    async def test_approval_step_auto_approves_without_store(self) -> None:
        """无 approval_store 时,ApprovalStep 自动通过(dev/test 模式)."""
        wf = (
            WorkflowBuilder()
            .id("wf-approval-auto")
            .step(id="draft", agent="a", prompt="draft email")
            .approval(
                id="approve",
                approver="manager@company.com",
                timeout_seconds=3600,
                depends_on=["draft"],
            )
            .step(
                id="send",
                agent="a",
                prompt="send email",
                depends_on=["approve"],
            )
            .build()
        )
        executor = FunctionExecutor(_echo_step)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        # approval step 完成且输出为空
        assert result.step_status["approve"] == StepStatus.COMPLETED
        assert result.step_results.get("approve", "") == ""
        # 后续 step 正常执行
        assert result.step_results["send"] == "out-send"

    @pytest.mark.asyncio
    async def test_approval_step_creates_request_in_store(self) -> None:
        """有 approval_store 时,ApprovalStep 创建 request."""
        store = InMemoryApprovalStore()

        # 自定义 orchestrator 注入 store
        wf = (
            WorkflowBuilder()
            .id("wf-approval-store")
            .step(id="draft", agent="a", prompt="p")
            .approval(
                id="approve",
                approver="manager@x.com",
                timeout_seconds=60,
                depends_on=["draft"],
            )
            .build()
        )

        orch = Orchestrator(executor=FunctionExecutor(_echo_step))
        orch._approval_store = store
        orch._current_workflow_id = "wf-approval-store"
        result = await orch.run(wf)

        assert result.status == WorkflowStatus.COMPLETED
        # store 中应该有 1 个 request
        reqs = await store.list_by_workflow("wf-approval-store")
        assert len(reqs) == 1
        assert reqs[0].step_id == "approve"
        assert reqs[0].approver == "manager@x.com"

    @pytest.mark.asyncio
    async def test_approval_step_output_is_empty(self) -> None:
        """ApprovalStep 的输出始终为空字符串."""
        wf = (
            WorkflowBuilder()
            .id("wf-approval-out")
            .approval(
                id="approve",
                approver="m",
                timeout_seconds=60,
            )
            .build()
        )
        executor = FunctionExecutor(_echo_step)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["approve"] == ""

    @pytest.mark.asyncio
    async def test_step_after_approval_uses_empty_output(self) -> None:
        """approval 之后的 step 收到空字符串输出."""

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "after":
                return f"after:{ctx.get('approve', 'MISSING')}"
            return f"out-{step.id}"

        wf = (
            WorkflowBuilder()
            .id("wf-approval-dep")
            .approval(id="approve", approver="m", timeout_seconds=60)
            .step(id="after", agent="a", prompt="p", depends_on=["approve"])
            .build()
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["after"] == "after:"


# ── 2. WorkflowBuilder.approval() ─────────────────────────────


class TestWorkflowBuilderApproval:
    """WorkflowBuilder.approval() — produces ApprovalStep."""

    def test_approval_adds_approval_step(self) -> None:
        """.approval() 添加 ApprovalStep."""
        wf = (
            WorkflowBuilder()
            .id("wf-b")
            .approval(
                id="approve",
                approver="manager@x.com",
                timeout_seconds=1800,
                on_timeout="approve",
            )
            .build()
        )
        assert len(wf.steps) == 1
        assert isinstance(wf.steps[0], ApprovalStep)
        assert wf.steps[0].approver == "manager@x.com"
        assert wf.steps[0].timeout_seconds == 1800
        assert wf.steps[0].on_timeout == "approve"

    def test_approval_default_on_timeout_is_reject(self) -> None:
        """不传 on_timeout 时默认 "reject"."""
        wf = (
            WorkflowBuilder()
            .id("wf-bd")
            .approval(id="a", approver="m")
            .build()
        )
        assert wf.steps[0].on_timeout == "reject"

    def test_approval_with_depends_on(self) -> None:
        """.approval() 支持 depends_on."""
        wf = (
            WorkflowBuilder()
            .id("wf-bd2")
            .step(id="s1", agent="a", prompt="p")
            .approval(
                id="approve",
                approver="m",
                depends_on=["s1"],
            )
            .build()
        )
        assert wf.steps[1].depends_on == ["s1"]


# ── 3. ApprovalStep + other step types ────────────────────────


class TestApprovalStepWithOtherTypes:
    """ApprovalStep combined with ConditionalStep / LoopStep / etc."""

    @pytest.mark.asyncio
    async def test_approval_after_branch(self) -> None:
        """branch → approval 组合."""
        wf = (
            WorkflowBuilder()
            .id("wf-branch-approval")
            .step(id="draft", agent="a", prompt="p")
            .branch(
                id="branch",
                agent="a",
                condition=lambda ctx: True,
                inner_steps=[
                    WorkflowStep(
                        id="review", name="Review", agent="a", prompt="p"
                    ),
                ],
                depends_on=["draft"],
            )
            .approval(
                id="approve",
                approver="m",
                timeout_seconds=60,
                depends_on=["branch"],
            )
            .build()
        )
        executor = FunctionExecutor(_echo_step)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_approval_then_timer(self) -> None:
        """approval → timer 组合."""
        wf = (
            WorkflowBuilder()
            .id("wf-approval-timer")
            .approval(id="approve", approver="m", timeout_seconds=60)
            .sleep(id="wait", duration_seconds=0, depends_on=["approve"])
            .step(id="final", agent="a", prompt="p", depends_on=["wait"])
            .build()
        )
        executor = FunctionExecutor(_echo_step)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_full_hitl_workflow(self) -> None:
        """完整 HITL workflow: draft → approve → send."""

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "draft":
                return "email draft content"
            if step.id == "send":
                return f"sent:{ctx.get('approve', '')}"
            return f"out-{step.id}"

        wf = (
            WorkflowBuilder()
            .id("wf-hitl-full")
            .step(id="draft", agent="writer", prompt="draft email")
            .approval(
                id="approve",
                approver="manager@company.com",
                timeout_seconds=3600,
                depends_on=["draft"],
            )
            .step(
                id="send",
                agent="sender",
                prompt="send email",
                depends_on=["approve"],
            )
            .build()
        )
        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["draft"] == "email draft content"
        assert result.step_results["approve"] == ""
        assert result.step_results["send"] == "sent:"


# ── 4. ApprovalStep timeout policy ────────────────────────────


class TestApprovalStepTimeoutPolicy:
    """ApprovalStep — on_timeout policy validation."""

    def test_on_timeout_reject_is_valid(self) -> None:
        """on_timeout="reject" 合法."""
        step = ApprovalStep(
            id="a",
            name="A",
            agent="x",
            prompt="p",
            approver="m",
            timeout_seconds=60,
            on_timeout="reject",
        )
        assert step.on_timeout == "reject"

    def test_on_timeout_approve_is_valid(self) -> None:
        """on_timeout="approve" 合法."""
        step = ApprovalStep(
            id="a",
            name="A",
            agent="x",
            prompt="p",
            approver="m",
            timeout_seconds=60,
            on_timeout="approve",
        )
        assert step.on_timeout == "approve"

    def test_on_timeout_abort_is_valid(self) -> None:
        """on_timeout="abort" 合法."""
        step = ApprovalStep(
            id="a",
            name="A",
            agent="x",
            prompt="p",
            approver="m",
            timeout_seconds=60,
            on_timeout="abort",
        )
        assert step.on_timeout == "abort"

    def test_on_timeout_invalid_raises(self) -> None:
        """on_timeout="invalid" 抛 ValueError."""
        with pytest.raises(ValueError):
            ApprovalStep(
                id="a",
                name="A",
                agent="x",
                prompt="p",
                approver="m",
                timeout_seconds=60,
                on_timeout="invalid",
            )


# ── 5. ApprovalStore integration ─────────────────────────────


class TestApprovalStoreIntegration:
    """ApprovalStore — end-to-end with workflow."""

    @pytest.mark.asyncio
    async def test_store_create_and_submit_decision(self) -> None:
        """store create → submit_decision → is_approved."""
        store = InMemoryApprovalStore()
        req = await store.create_request(
            workflow_id="wf-1",
            step_id="approve",
            approver="manager@x.com",
            timeout_seconds=3600,
        )
        assert req.is_pending()

        await store.submit_decision(
            req.request_id,
            decision="approved",
            user_id="manager@x.com",
            comment="Looks good",
        )
        fetched = await store.get_request(req.request_id)
        assert fetched is not None
        assert fetched.is_approved()
        assert fetched.decided_by == "manager@x.com"

    @pytest.mark.asyncio
    async def test_store_pending_list_filters_by_approver(self) -> None:
        """list_pending 按 approver 过滤."""
        store = InMemoryApprovalStore()
        await store.create_request("wf", "s1", "alice", 60)
        await store.create_request("wf", "s2", "bob", 60)
        await store.create_request("wf", "s3", "alice", 60)

        alice_pending = await store.list_pending("alice")
        bob_pending = await store.list_pending("bob")
        assert len(alice_pending) == 2
        assert len(bob_pending) == 1

    @pytest.mark.asyncio
    async def test_store_timeout_marks_timed_out(self) -> None:
        """check_timeout 标记过期 request 为 timed_out."""
        store = InMemoryApprovalStore()
        req = await store.create_request(
            "wf",
            "s",
            "a",
            timeout_seconds=1,
        )
        req.created_at = __import__("time").time() - 100

        result = await store.check_timeout(req.request_id)
        assert result is True

        fetched = await store.get_request(req.request_id)
        assert fetched is not None
        assert fetched.is_timed_out()
