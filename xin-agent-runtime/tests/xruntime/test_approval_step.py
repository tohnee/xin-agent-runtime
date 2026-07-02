# -*- coding: utf-8 -*-
"""TDD tests for P3-B Task 1: ApprovalStep + ApprovalStore.

Covers workflow-level Human-in-the-Loop (HITL): an :class:`ApprovalStep`
pauses the workflow until a human approves or rejects it.  An
:class:`ApprovalStore` tracks pending/approved/rejected requests with
TTL-based timeout.

Components under test:

* :class:`ApprovalRequest` — data model for a single approval request.
* :class:`ApprovalDecision` — approved / rejected / timed_out.
* :class:`ApprovalStore` — ABC for approval storage backends.
* :class:`InMemoryApprovalStore` — dict-backed store for tests / dev.
* :class:`ApprovalStep` — workflow step that pauses for approval.
"""
from __future__ import annotations

import time
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


# ── 1. ApprovalRequest data model ─────────────────────────────


class TestApprovalRequest:
    """ApprovalRequest — data model."""

    def test_request_construction(self) -> None:
        """构造: workflow_id + step_id + approver + timeout."""
        from xruntime._runtime._workflow._approval import (
            ApprovalRequest,
        )

        req = ApprovalRequest(
            request_id="apr-123",
            workflow_id="wf-1",
            step_id="approve-email",
            approver="manager@company.com",
            timeout_seconds=3600,
        )
        assert req.request_id == "apr-123"
        assert req.workflow_id == "wf-1"
        assert req.step_id == "approve-email"
        assert req.approver == "manager@company.com"
        assert req.timeout_seconds == 3600
        assert req.decision == "pending"
        assert req.created_at > 0

    def test_request_default_decision_is_pending(self) -> None:
        """默认 decision="pending"."""
        from xruntime._runtime._workflow._approval import (
            ApprovalRequest,
        )

        req = ApprovalRequest(
            request_id="r1",
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
        )
        assert req.decision == "pending"
        assert req.is_pending() is True
        assert req.is_approved() is False
        assert req.is_rejected() is False
        assert req.is_timed_out() is False

    def test_request_is_approved(self) -> None:
        """decision="approved" → is_approved()=True."""
        from xruntime._runtime._workflow._approval import (
            ApprovalRequest,
        )

        req = ApprovalRequest(
            request_id="r1",
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
            decision="approved",
        )
        assert req.is_approved() is True
        assert req.is_pending() is False

    def test_request_is_rejected(self) -> None:
        """decision="rejected" → is_rejected()=True."""
        from xruntime._runtime._workflow._approval import (
            ApprovalRequest,
        )

        req = ApprovalRequest(
            request_id="r1",
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
            decision="rejected",
        )
        assert req.is_rejected() is True
        assert req.is_approved() is False

    def test_request_is_timed_out(self) -> None:
        """decision="timed_out" → is_timed_out()=True."""
        from xruntime._runtime._workflow._approval import (
            ApprovalRequest,
        )

        req = ApprovalRequest(
            request_id="r1",
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
            decision="timed_out",
        )
        assert req.is_timed_out() is True

    def test_request_is_resolved_when_decided(self) -> None:
        """is_resolved()=True 当 decision 是 approved/rejected/timed_out."""
        from xruntime._runtime._workflow._approval import (
            ApprovalRequest,
        )

        # pending → not resolved
        pending = ApprovalRequest(
            request_id="r1",
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
        )
        assert pending.is_resolved() is False

        # approved → resolved
        approved = ApprovalRequest(
            request_id="r2",
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
            decision="approved",
        )
        assert approved.is_resolved() is True

        # rejected → resolved
        rejected = ApprovalRequest(
            request_id="r3",
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
            decision="rejected",
        )
        assert rejected.is_resolved() is True

        # timed_out → resolved
        timed_out = ApprovalRequest(
            request_id="r4",
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
            decision="timed_out",
        )
        assert timed_out.is_resolved() is True

    def test_request_has_expired_when_past_timeout(self) -> None:
        """创建时间 + timeout < now → has_expired()=True."""
        from xruntime._runtime._workflow._approval import (
            ApprovalRequest,
        )

        # 创建一个已过期的 request(created_at 在过去)
        req = ApprovalRequest(
            request_id="r1",
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
            created_at=time.time() - 120,  # 2 分钟前创建,60 秒超时
        )
        assert req.has_expired() is True

    def test_request_not_expired_within_timeout(self) -> None:
        """创建时间 + timeout > now → has_expired()=False."""
        from xruntime._runtime._workflow._approval import (
            ApprovalRequest,
        )

        req = ApprovalRequest(
            request_id="r1",
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=3600,
            created_at=time.time(),  # 刚创建
        )
        assert req.has_expired() is False


# ── 2. InMemoryApprovalStore CRUD ─────────────────────────────


class TestInMemoryApprovalStoreCRUD:
    """InMemoryApprovalStore — create / get / submit_decision."""

    @pytest.mark.asyncio
    async def test_create_request_returns_request_with_id(self) -> None:
        """create_request 返回带 request_id 的 ApprovalRequest."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        req = await store.create_request(
            workflow_id="wf-1",
            step_id="approve-email",
            approver="manager@company.com",
            timeout_seconds=3600,
        )
        assert req.request_id.startswith("apr-")
        assert req.workflow_id == "wf-1"
        assert req.step_id == "approve-email"
        assert req.approver == "manager@company.com"
        assert req.is_pending()

    @pytest.mark.asyncio
    async def test_get_request_returns_created(self) -> None:
        """create → get 返回相同的 request."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        req = await store.create_request(
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
        )
        fetched = await store.get_request(req.request_id)
        assert fetched is not None
        assert fetched.request_id == req.request_id
        assert fetched.workflow_id == "wf"

    @pytest.mark.asyncio
    async def test_get_unknown_request_returns_none(self) -> None:
        """get 不存在的 request_id 返回 None."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        result = await store.get_request("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_submit_decision_approved(self) -> None:
        """submit_decision("approved") 更新 request 状态."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        req = await store.create_request(
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
        )
        await store.submit_decision(
            req.request_id,
            decision="approved",
            user_id="manager",
        )
        fetched = await store.get_request(req.request_id)
        assert fetched is not None
        assert fetched.is_approved()
        assert fetched.decided_by == "manager"

    @pytest.mark.asyncio
    async def test_submit_decision_rejected(self) -> None:
        """submit_decision("rejected") 更新 request 状态."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        req = await store.create_request(
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
        )
        await store.submit_decision(
            req.request_id,
            decision="rejected",
            user_id="manager",
            comment="Not good enough",
        )
        fetched = await store.get_request(req.request_id)
        assert fetched is not None
        assert fetched.is_rejected()
        assert fetched.decided_by == "manager"
        assert fetched.comment == "Not good enough"

    @pytest.mark.asyncio
    async def test_submit_decision_unknown_request_raises(self) -> None:
        """submit_decision 对不存在的 request_id 抛 KeyError."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        with pytest.raises(KeyError):
            await store.submit_decision(
                "nonexistent",
                decision="approved",
                user_id="m",
            )

    @pytest.mark.asyncio
    async def test_submit_decision_invalid_decision_raises(
        self,
    ) -> None:
        """submit_decision 传入非法 decision 抛 ValueError."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        req = await store.create_request(
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
        )
        with pytest.raises(ValueError):
            await store.submit_decision(
                req.request_id,
                decision="maybe",
                user_id="m",
            )

    @pytest.mark.asyncio
    async def test_submit_decision_on_already_decided_raises(
        self,
    ) -> None:
        """对已决策的 request 再次 submit 抛 RuntimeError."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        req = await store.create_request(
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=60,
        )
        await store.submit_decision(
            req.request_id,
            decision="approved",
            user_id="m",
        )
        with pytest.raises(RuntimeError):
            await store.submit_decision(
                req.request_id,
                decision="rejected",
                user_id="m2",
            )


# ── 3. InMemoryApprovalStore listing ──────────────────────────


class TestInMemoryApprovalStoreListing:
    """InMemoryApprovalStore — list_pending / list_by_workflow."""

    @pytest.mark.asyncio
    async def test_list_pending_returns_only_pending(self) -> None:
        """list_pending(approver) 只返回 pending 请求."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        r1 = await store.create_request(
            "wf-1",
            "s1",
            "alice",
            60,
        )
        r2 = await store.create_request(
            "wf-2",
            "s2",
            "alice",
            60,
        )
        r3 = await store.create_request(
            "wf-3",
            "s3",
            "bob",
            60,
        )

        # alice 有 2 个 pending
        pending = await store.list_pending("alice")
        assert len(pending) == 2
        ids = {r.request_id for r in pending}
        assert r1.request_id in ids
        assert r2.request_id in ids

        # approve r1 → alice 有 1 个 pending
        await store.submit_decision(
            r1.request_id,
            decision="approved",
            user_id="alice",
        )
        pending = await store.list_pending("alice")
        assert len(pending) == 1
        assert pending[0].request_id == r2.request_id

    @pytest.mark.asyncio
    async def test_list_pending_empty_when_none(self) -> None:
        """list_pending 在 approver 无 pending 请求时返回空列表."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        pending = await store.list_pending("nobody")
        assert pending == []

    @pytest.mark.asyncio
    async def test_list_by_workflow(self) -> None:
        """list_by_workflow 返回 workflow 的所有请求."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        await store.create_request("wf-1", "s1", "a", 60)
        await store.create_request("wf-1", "s2", "b", 60)
        await store.create_request("wf-2", "s3", "c", 60)

        wf1_reqs = await store.list_by_workflow("wf-1")
        assert len(wf1_reqs) == 2
        wf2_reqs = await store.list_by_workflow("wf-2")
        assert len(wf2_reqs) == 1

    @pytest.mark.asyncio
    async def test_list_by_workflow_empty(self) -> None:
        """list_by_workflow 对不存在的 workflow 返回空列表."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        result = await store.list_by_workflow("nonexistent")
        assert result == []


# ── 4. InMemoryApprovalStore timeout ─────────────────────────


class TestInMemoryApprovalStoreTimeout:
    """InMemoryApprovalStore — check_timeout."""

    @pytest.mark.asyncio
    async def test_check_timeout_applies_on_timeout_policy(self) -> None:
        """check_timeout 对已过期 request 自动应用 on_timeout 策略."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        # 创建一个已过期的 request(创建时间在过去)
        req = await store.create_request(
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=1,
        )
        # 手动修改 created_at 让它过期
        req.created_at = time.time() - 10
        store._store[req.request_id] = req

        # check_timeout → 应用 on_timeout 策略
        result = await store.check_timeout(req.request_id)
        assert result is True  # 已超时

        fetched = await store.get_request(req.request_id)
        assert fetched is not None
        assert fetched.is_timed_out()  # 自动标记为 timed_out

    @pytest.mark.asyncio
    async def test_check_timeout_not_expired_returns_false(self) -> None:
        """check_timeout 对未过期 request 返回 False."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        req = await store.create_request(
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=3600,
        )
        result = await store.check_timeout(req.request_id)
        assert result is False

    @pytest.mark.asyncio
    async def test_check_timeout_unknown_request_returns_false(
        self,
    ) -> None:
        """check_timeout 对不存在的 request_id 返回 False."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        result = await store.check_timeout("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_check_timeout_already_decided_returns_false(
        self,
    ) -> None:
        """check_timeout 对已决策的 request 返回 False(即使已过期)."""
        from xruntime._runtime._workflow._approval import (
            InMemoryApprovalStore,
        )

        store = InMemoryApprovalStore()
        req = await store.create_request(
            workflow_id="wf",
            step_id="s",
            approver="a",
            timeout_seconds=1,
        )
        # 先 approve
        await store.submit_decision(
            req.request_id,
            decision="approved",
            user_id="m",
        )
        # 手动让 created_at 过期
        req.created_at = time.time() - 100
        # check_timeout 应返回 False(已决策,不再超时)
        result = await store.check_timeout(req.request_id)
        assert result is False


# ── 6. ApprovalStore ABC ───────────────────────────────────────


class TestApprovalStoreABC:
    """ApprovalStore — ABC raises NotImplementedError."""

    @pytest.mark.asyncio
    async def test_abc_create_request_raises(self) -> None:
        """ABC create_request 抛 NotImplementedError."""
        from xruntime._runtime._workflow._approval import ApprovalStore

        store = ApprovalStore()
        with pytest.raises(NotImplementedError):
            await store.create_request("wf", "s", "a", 60)

    @pytest.mark.asyncio
    async def test_abc_get_request_raises(self) -> None:
        """ABC get_request 抛 NotImplementedError."""
        from xruntime._runtime._workflow._approval import ApprovalStore

        store = ApprovalStore()
        with pytest.raises(NotImplementedError):
            await store.get_request("x")

    @pytest.mark.asyncio
    async def test_abc_submit_decision_raises(self) -> None:
        """ABC submit_decision 抛 NotImplementedError."""
        from xruntime._runtime._workflow._approval import ApprovalStore

        store = ApprovalStore()
        with pytest.raises(NotImplementedError):
            await store.submit_decision(
                "x",
                decision="approved",
                user_id="m",
            )

    @pytest.mark.asyncio
    async def test_abc_list_pending_raises(self) -> None:
        """ABC list_pending 抛 NotImplementedError."""
        from xruntime._runtime._workflow._approval import ApprovalStore

        store = ApprovalStore()
        with pytest.raises(NotImplementedError):
            await store.list_pending("a")

    @pytest.mark.asyncio
    async def test_abc_list_by_workflow_raises(self) -> None:
        """ABC list_by_workflow 抛 NotImplementedError."""
        from xruntime._runtime._workflow._approval import ApprovalStore

        store = ApprovalStore()
        with pytest.raises(NotImplementedError):
            await store.list_by_workflow("wf")

    @pytest.mark.asyncio
    async def test_abc_check_timeout_raises(self) -> None:
        """ABC check_timeout 抛 NotImplementedError."""
        from xruntime._runtime._workflow._approval import ApprovalStore

        store = ApprovalStore()
        with pytest.raises(NotImplementedError):
            await store.check_timeout("x")


# ── 5. ApprovalStep construction ─────────────────────────────


class TestApprovalStepConstruction:
    """ApprovalStep — construction."""

    def test_approval_step_with_approver_and_timeout(self) -> None:
        """构造: approver + timeout_seconds + on_timeout."""
        from xruntime._runtime._workflow._approval import ApprovalStep

        step = ApprovalStep(
            id="approve-email",
            name="Approve Email",
            agent="a",
            prompt="wait for approval",
            approver="manager@company.com",
            timeout_seconds=3600,
            on_timeout="reject",
        )
        assert step.id == "approve-email"
        assert step.approver == "manager@company.com"
        assert step.timeout_seconds == 3600
        assert step.on_timeout == "reject"

    def test_approval_step_inherits_workflow_step_fields(self) -> None:
        """ApprovalStep 继承 WorkflowStep 的所有字段."""
        from xruntime._runtime._workflow._approval import ApprovalStep

        step = ApprovalStep(
            id="ap",
            name="A",
            agent="a",
            prompt="p",
            approver="m",
            timeout_seconds=60,
            depends_on=["s1"],
            on_failure="continue",
            max_retries=2,
        )
        assert step.depends_on == ["s1"]
        assert step.on_failure == "continue"
        assert step.max_retries == 2

    def test_approval_step_default_on_timeout_is_reject(self) -> None:
        """默认 on_timeout="reject"."""
        from xruntime._runtime._workflow._approval import ApprovalStep

        step = ApprovalStep(
            id="ap",
            name="A",
            agent="a",
            prompt="p",
            approver="m",
            timeout_seconds=60,
        )
        assert step.on_timeout == "reject"

    def test_approval_step_invalid_on_timeout_raises(self) -> None:
        """on_timeout 非法值抛 ValueError."""
        from xruntime._runtime._workflow._approval import ApprovalStep

        with pytest.raises(ValueError):
            ApprovalStep(
                id="ap",
                name="A",
                agent="a",
                prompt="p",
                approver="m",
                timeout_seconds=60,
                on_timeout="invalid",
            )
