# -*- coding: utf-8 -*-
"""TDD tests for the Workflow Checkpoint module (P2 Task 3.1).

Covers:

1. :class:`Checkpoint` — pydantic model, serialization, TTL.
2. :class:`CheckpointStore` ABC + :class:`InMemoryCheckpointStore` —
   save / load / list / latest / delete / TTL eviction.
3. :class:`CheckpointedOrchestrator` — wraps the in-memory
   :class:`Orchestrator` with per-layer checkpoint persistence and a
   ``resume(workflow_id)`` entry point.
4. :class:`WorkflowConfig` — defaults + env-var override + wiring
   into :class:`XRuntimeConfig`.

Design rationale: the existing :class:`Orchestrator` is an in-memory
DAG engine with no durability.  The checkpoint module is a *wrapper*
that persists state after each topological layer completes, so a
crash mid-workflow loses at most the in-flight layer.  Resume loads
the latest checkpoint, rebuilds the :class:`WorkflowResult`, and
continues with the next pending layers.
"""
from __future__ import annotations

import time
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


# ── helpers ────────────────────────────────────────────────────────


def _make_linear_workflow(wf_id: str = "wf-1") -> Workflow:
    """A 3-step linear workflow: s1 → s2 → s3."""
    return Workflow(
        id=wf_id,
        name="linear",
        steps=[
            WorkflowStep(id="s1", name="step-1", agent="a1", prompt="p1"),
            WorkflowStep(
                id="s2",
                name="step-2",
                agent="a2",
                prompt="p2",
                depends_on=["s1"],
            ),
            WorkflowStep(
                id="s3",
                name="step-3",
                agent="a3",
                prompt="p3",
                depends_on=["s2"],
            ),
        ],
    )


def _make_parallel_workflow(wf_id: str = "wf-par") -> Workflow:
    """A workflow with a parallel layer: s1 → {s2a, s2b} → s3."""
    return Workflow(
        id=wf_id,
        name="parallel",
        steps=[
            WorkflowStep(id="s1", name="step-1", agent="a1", prompt="p1"),
            WorkflowStep(
                id="s2a",
                name="step-2a",
                agent="a2",
                prompt="p2a",
                depends_on=["s1"],
            ),
            WorkflowStep(
                id="s2b",
                name="step-2b",
                agent="a3",
                prompt="p2b",
                depends_on=["s1"],
            ),
            WorkflowStep(
                id="s3",
                name="step-3",
                agent="a4",
                prompt="p3",
                depends_on=["s2a", "s2b"],
            ),
        ],
    )


async def _echo_executor(
    step: WorkflowStep,
    context: dict[str, Any],
) -> str:
    """Trivial executor: returns '<step_id>:<dep_outputs>'."""
    deps = ",".join(context.get(d, "") for d in step.depends_on)
    return f"{step.id}:{deps}"


# ── 1. Checkpoint model ────────────────────────────────────────────


class TestCheckpoint:
    """Checkpoint — pydantic data model with TTL + chain."""

    def test_basic_construction(self) -> None:
        from xruntime._runtime._workflow import Checkpoint

        cp = Checkpoint(
            workflow_id="wf-1",
            workflow_name="linear",
            step_id="s1",
            step_name="step-1",
            step_results={"s1": "s1:"},
            step_status={"s1": StepStatus.COMPLETED},
            context={"s1": "s1:"},
            status="ACTIVE",
        )
        assert cp.workflow_id == "wf-1"
        assert cp.step_id == "s1"
        assert cp.step_results == {"s1": "s1:"}
        assert cp.status == "ACTIVE"
        assert cp.parent_checkpoint_id is None
        assert cp.checkpoint_id  # auto-generated
        assert cp.created_at > 0

    def test_auto_generated_checkpoint_id_is_unique(self) -> None:
        from xruntime._runtime._workflow import Checkpoint

        cp1 = Checkpoint(
            workflow_id="wf",
            workflow_name="n",
            step_id="",
            step_name="",
            step_results={},
            step_status={},
            context={},
            status="ACTIVE",
        )
        cp2 = Checkpoint(
            workflow_id="wf",
            workflow_name="n",
            step_id="",
            step_name="",
            step_results={},
            step_status={},
            context={},
            status="ACTIVE",
        )
        assert cp1.checkpoint_id != cp2.checkpoint_id

    def test_is_expired_false_when_no_ttl(self) -> None:
        from xruntime._runtime._workflow import Checkpoint

        cp = Checkpoint(
            workflow_id="wf",
            workflow_name="n",
            step_id="",
            step_name="",
            step_results={},
            step_status={},
            context={},
            status="ACTIVE",
        )
        assert not cp.is_expired()

    def test_is_expired_true_when_past_ttl(self) -> None:
        from xruntime._runtime._workflow import Checkpoint

        cp = Checkpoint(
            workflow_id="wf",
            workflow_name="n",
            step_id="",
            step_name="",
            step_results={},
            step_status={},
            context={},
            status="ACTIVE",
            expires_at=time.time() - 1.0,
        )
        assert cp.is_expired()

    def test_is_expired_false_when_within_ttl(self) -> None:
        from xruntime._runtime._workflow import Checkpoint

        cp = Checkpoint(
            workflow_id="wf",
            workflow_name="n",
            step_id="",
            step_name="",
            step_results={},
            step_status={},
            context={},
            status="ACTIVE",
            expires_at=time.time() + 3600.0,
        )
        assert not cp.is_expired()

    def test_serialization_round_trip(self) -> None:
        """Checkpoint must serialize to / from a dict for Redis storage."""
        from xruntime._runtime._workflow import Checkpoint

        cp = Checkpoint(
            workflow_id="wf-1",
            workflow_name="linear",
            step_id="s2",
            step_name="step-2",
            step_results={"s1": "out1", "s2": "out2"},
            step_status={"s1": "COMPLETED", "s2": "COMPLETED"},
            context={"s1": "out1", "s2": "out2"},
            status="ACTIVE",
            parent_checkpoint_id="cp-parent-1",
        )
        data = cp.to_dict()
        assert isinstance(data, dict)
        # Round-trip
        restored = Checkpoint.from_dict(data)
        assert restored.checkpoint_id == cp.checkpoint_id
        assert restored.workflow_id == cp.workflow_id
        assert restored.step_id == cp.step_id
        assert restored.step_results == cp.step_results
        assert restored.step_status == cp.step_status
        assert restored.context == cp.context
        assert restored.status == cp.status
        assert restored.parent_checkpoint_id == cp.parent_checkpoint_id


# ── 2. InMemoryCheckpointStore ─────────────────────────────────────


class TestInMemoryCheckpointStore:
    """InMemoryCheckpointStore — dict-backed store for tests."""

    def _make_store(self):
        from xruntime._runtime._workflow import InMemoryCheckpointStore

        return InMemoryCheckpointStore()

    def _make_checkpoint(
        self,
        wf_id: str = "wf-1",
        step_id: str = "s1",
        parent: str | None = None,
        status: str = "ACTIVE",
    ) -> Any:
        from xruntime._runtime._workflow import Checkpoint

        return Checkpoint(
            workflow_id=wf_id,
            workflow_name="n",
            step_id=step_id,
            step_name=step_id,
            step_results={step_id: "out"} if step_id else {},
            step_status={step_id: "COMPLETED"} if step_id else {},
            context={step_id: "out"} if step_id else {},
            status=status,
            parent_checkpoint_id=parent,
        )

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(self) -> None:
        store = self._make_store()
        cp = self._make_checkpoint()
        cp_id = await store.save(cp)
        assert cp_id == cp.checkpoint_id

        loaded = await store.load(cp_id)
        assert loaded is not None
        assert loaded.checkpoint_id == cp.checkpoint_id
        assert loaded.workflow_id == cp.workflow_id

    @pytest.mark.asyncio
    async def test_load_unknown_returns_none(self) -> None:
        store = self._make_store()
        assert await store.load("nope") is None

    @pytest.mark.asyncio
    async def test_latest_for_workflow_returns_most_recent(self) -> None:
        store = self._make_store()
        cp1 = self._make_checkpoint(step_id="s1")
        await store.save(cp1)
        # Tiny sleep so created_at is strictly greater for cp2
        time.sleep(0.01)
        cp2 = self._make_checkpoint(step_id="s2", parent=cp1.checkpoint_id)
        await store.save(cp2)

        latest = await store.latest_for_workflow("wf-1")
        assert latest is not None
        assert latest.checkpoint_id == cp2.checkpoint_id

    @pytest.mark.asyncio
    async def test_latest_for_workflow_unknown_returns_none(self) -> None:
        store = self._make_store()
        assert await store.latest_for_workflow("nope") is None

    @pytest.mark.asyncio
    async def test_list_by_workflow_returns_in_order(self) -> None:
        store = self._make_store()
        cp1 = self._make_checkpoint(step_id="s1")
        await store.save(cp1)
        time.sleep(0.01)
        cp2 = self._make_checkpoint(step_id="s2", parent=cp1.checkpoint_id)
        await store.save(cp2)
        time.sleep(0.01)
        cp3 = self._make_checkpoint(step_id="s3", parent=cp2.checkpoint_id)
        await store.save(cp3)

        cps = await store.list_by_workflow("wf-1")
        assert len(cps) == 3
        # Ordered by created_at ascending (oldest first)
        assert cps[0].checkpoint_id == cp1.checkpoint_id
        assert cps[2].checkpoint_id == cp3.checkpoint_id

    @pytest.mark.asyncio
    async def test_list_by_workflow_unknown_returns_empty(self) -> None:
        store = self._make_store()
        assert await store.list_by_workflow("nope") == []

    @pytest.mark.asyncio
    async def test_delete_removes_checkpoint(self) -> None:
        store = self._make_store()
        cp = self._make_checkpoint()
        cp_id = await store.save(cp)
        assert await store.delete(cp_id) is True
        assert await store.load(cp_id) is None

    @pytest.mark.asyncio
    async def test_delete_unknown_returns_false(self) -> None:
        store = self._make_store()
        assert await store.delete("nope") is False

    @pytest.mark.asyncio
    async def test_delete_by_workflow_removes_all(self) -> None:
        store = self._make_store()
        cp1 = self._make_checkpoint(step_id="s1")
        await store.save(cp1)
        cp2 = self._make_checkpoint(step_id="s2", parent=cp1.checkpoint_id)
        await store.save(cp2)

        count = await store.delete_by_workflow("wf-1")
        assert count == 2
        assert await store.list_by_workflow("wf-1") == []

    @pytest.mark.asyncio
    async def test_load_expired_returns_none(self) -> None:
        """Expired checkpoints are treated as missing on load."""
        store = self._make_store()
        cp = self._make_checkpoint()
        cp.expires_at = time.time() - 1.0  # already expired
        cp_id = await store.save(cp)

        assert await store.load(cp_id) is None

    @pytest.mark.asyncio
    async def test_latest_skips_expired(self) -> None:
        """latest_for_workflow must skip expired entries."""
        store = self._make_store()
        cp1 = self._make_checkpoint(step_id="s1")
        cp1.expires_at = time.time() - 1.0  # expired
        await store.save(cp1)
        time.sleep(0.01)
        cp2 = self._make_checkpoint(step_id="s2", parent=cp1.checkpoint_id)
        await store.save(cp2)

        latest = await store.latest_for_workflow("wf-1")
        assert latest is not None
        assert latest.checkpoint_id == cp2.checkpoint_id


# ── 3. CheckpointedOrchestrator ────────────────────────────────────


class TestCheckpointedOrchestrator:
    """CheckpointedOrchestrator — wraps Orchestrator with durability."""

    def _make_orchestrator(self):
        from xruntime._runtime._workflow import (
            CheckpointedOrchestrator,
            InMemoryCheckpointStore,
        )

        return CheckpointedOrchestrator(
            executor=_echo_executor,
            store=InMemoryCheckpointStore(),
        )

    @pytest.mark.asyncio
    async def test_run_linear_workflow_saves_checkpoints(self) -> None:
        orch = self._make_orchestrator()
        wf = _make_linear_workflow()

        result = await orch.run(wf)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results == {
            "s1": "s1:",
            "s2": "s2:s1:",
            "s3": "s3:s2:s1:",
        }

        # A checkpoint must be saved after each layer (3 steps → 3 layers
        # in a linear workflow → 3 checkpoints, plus possibly an initial).
        cps = await orch.store.list_by_workflow(wf.id)
        assert len(cps) >= 3
        # The latest checkpoint must be COMPLETED
        latest = await orch.store.latest_for_workflow(wf.id)
        assert latest is not None
        assert latest.status == "COMPLETED"

    @pytest.mark.asyncio
    async def test_run_parallel_workflow_saves_checkpoint_per_layer(
        self,
    ) -> None:
        orch = self._make_orchestrator()
        wf = _make_parallel_workflow()

        result = await orch.run(wf)

        assert result.status == WorkflowStatus.COMPLETED
        # 3 layers: [s1], [s2a, s2b], [s3]
        cps = await orch.store.list_by_workflow(wf.id)
        assert len(cps) >= 3

    @pytest.mark.asyncio
    async def test_checkpoint_chain_links_parent_ids(self) -> None:
        orch = self._make_orchestrator()
        wf = _make_linear_workflow()

        await orch.run(wf)

        cps = await orch.store.list_by_workflow(wf.id)
        # The 2nd checkpoint's parent must be the 1st's id, etc.
        assert cps[1].parent_checkpoint_id == cps[0].checkpoint_id
        assert cps[2].parent_checkpoint_id == cps[1].checkpoint_id

    @pytest.mark.asyncio
    async def test_resume_completed_workflow_returns_result(self) -> None:
        orch = self._make_orchestrator()
        wf = _make_linear_workflow()

        # First run completes
        await orch.run(wf)

        # Resume should return the completed result without re-executing
        result = await orch.resume(wf)
        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results == {
            "s1": "s1:",
            "s2": "s2:s1:",
            "s3": "s3:s2:s1:",
        }

    @pytest.mark.asyncio
    async def test_resume_unknown_workflow_returns_none(self) -> None:
        orch = self._make_orchestrator()
        wf = _make_linear_workflow()
        assert await orch.resume(wf) is None

    @pytest.mark.asyncio
    async def test_resume_mid_workflow_continues_execution(self) -> None:
        """Simulate a crash after step s1, then resume."""
        from xruntime._runtime._workflow import Checkpoint

        orch = self._make_orchestrator()
        wf = _make_linear_workflow()

        # Manually save a checkpoint as if s1 just completed and the
        # process crashed before s2 ran.
        cp = Checkpoint(
            workflow_id=wf.id,
            workflow_name=wf.name,
            step_id="s1",
            step_name="step-1",
            step_results={"s1": "s1:"},
            step_status={"s1": "COMPLETED"},
            context={"s1": "s1:"},
            status="ACTIVE",
        )
        await orch.store.save(cp)

        # Resume — should run s2 and s3, reusing s1's output.
        result = await orch.resume(wf)

        assert result is not None
        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results == {
            "s1": "s1:",  # reused from checkpoint
            "s2": "s2:s1:",
            "s3": "s3:s2:s1:",
        }
        # s1 must NOT have been re-executed (its output was reused)
        # We can verify this by checking that the step_status for s1
        # was carried over from the checkpoint.

    @pytest.mark.asyncio
    async def test_resume_failed_workflow_does_not_re_run(self) -> None:
        """A FAILED checkpoint should not be re-run on resume."""
        from xruntime._runtime._workflow import Checkpoint

        orch = self._make_orchestrator()
        wf = _make_linear_workflow()

        cp = Checkpoint(
            workflow_id=wf.id,
            workflow_name=wf.name,
            step_id="s1",
            step_name="step-1",
            step_results={},
            step_status={"s1": "FAILED"},
            context={},
            status="FAILED",
        )
        await orch.store.save(cp)

        result = await orch.resume(wf)
        assert result is not None
        assert result.status == WorkflowStatus.FAILED

    @pytest.mark.asyncio
    async def test_failed_step_saves_failed_checkpoint(self) -> None:
        """When a step fails with abort strategy, a FAILED checkpoint
        is persisted so resume doesn't re-run the workflow."""
        from xruntime._runtime._workflow import (
            CheckpointedOrchestrator,
            InMemoryCheckpointStore,
        )

        async def failing_executor(
            step: WorkflowStep,
            context: dict[str, Any],
        ) -> str:
            if step.id == "s2":
                raise RuntimeError("boom")
            return f"{step.id}:ok"

        orch = CheckpointedOrchestrator(
            executor=failing_executor,
            store=InMemoryCheckpointStore(),
        )
        wf = _make_linear_workflow()

        result = await orch.run(wf)
        assert result.status == WorkflowStatus.FAILED

        latest = await orch.store.latest_for_workflow(wf.id)
        assert latest is not None
        assert latest.status == "FAILED"

    @pytest.mark.asyncio
    async def test_context_preserved_across_checkpoints(self) -> None:
        orch = self._make_orchestrator()
        wf = _make_linear_workflow()

        await orch.run(wf)

        cps = await orch.store.list_by_workflow(wf.id)
        # Each checkpoint's context must include all prior step outputs
        assert cps[0].context == {"s1": "s1:"}
        assert cps[1].context == {"s1": "s1:", "s2": "s2:s1:"}
        assert cps[2].context == {
            "s1": "s1:",
            "s2": "s2:s1:",
            "s3": "s3:s2:s1:",
        }


# ── 4. WorkflowConfig ─────────────────────────────────────────────


class TestWorkflowConfig:
    """WorkflowConfig — pydantic config + XRuntimeConfig wiring."""

    def test_defaults(self) -> None:
        from xruntime._runtime._workflow import WorkflowConfig

        cfg = WorkflowConfig()
        assert cfg.enabled is False
        assert cfg.default_checkpoint_ttl_seconds > 0
        assert cfg.store_backend == "memory"

    def test_xruntime_config_has_workflow_field(self) -> None:
        from xruntime._config import XRuntimeConfig
        from xruntime._runtime._workflow import WorkflowConfig

        cfg = XRuntimeConfig()
        assert isinstance(cfg.workflow, WorkflowConfig)
        assert cfg.workflow.enabled is False

    def test_env_override_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from xruntime._config import _apply_env_overrides, XRuntimeConfig

        monkeypatch.setenv("XRUNTIME_WORKFLOW_ENABLED", "true")
        monkeypatch.setenv(
            "XRUNTIME_WORKFLOW_DEFAULT_CHECKPOINT_TTL_SECONDS", "7200"
        )
        cfg = _apply_env_overrides(XRuntimeConfig())
        assert cfg.workflow.enabled is True
        assert cfg.workflow.default_checkpoint_ttl_seconds == 7200

    def test_workflow_config_can_be_enabled_inline(self) -> None:
        from xruntime._config import XRuntimeConfig
        from xruntime._runtime._workflow import WorkflowConfig

        cfg = XRuntimeConfig(
            workflow=WorkflowConfig(
                enabled=True,
                default_checkpoint_ttl_seconds=3600,
                store_backend="redis",
            ),
        )
        assert cfg.workflow.enabled is True
        assert cfg.workflow.default_checkpoint_ttl_seconds == 3600
        assert cfg.workflow.store_backend == "redis"
