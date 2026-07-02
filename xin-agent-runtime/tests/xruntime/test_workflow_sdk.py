# -*- coding: utf-8 -*-
"""TDD tests for the Workflow SDK public API (P2 Task 3.4).

Covers the high-level convenience layer over the existing
:class:`Orchestrator` + :class:`CheckpointedOrchestrator`:

1. :class:`WorkflowBuilder` — fluent API for constructing
   :class:`Workflow` instances programmatically.
2. :class:`FunctionExecutor` — wraps a sync callable into an async
   :class:`StepExecutor` for tests / evals / simple use cases.
3. :func:`run_workflow` — one-shot convenience that runs a workflow
   with optional checkpointing.
4. :func:`resume_workflow` — resume a checkpointed workflow from its
   latest checkpoint.
5. :func:`load_workflow_from_file` — load a YAML workflow definition
   from disk.
6. Public exports — :mod:`xruntime._runtime._workflow` re-exports the
   SDK surface.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    Orchestrator,
    StepExecutor,
    StepStatus,
    Workflow,
    WorkflowResult,
    WorkflowStatus,
    WorkflowStep,
)
from xruntime._runtime._workflow import (
    Checkpoint,
    CheckpointedOrchestrator,
    InMemoryCheckpointStore,
)


# ── 1. WorkflowBuilder ─────────────────────────────────────────────


class TestWorkflowBuilder:
    """WorkflowBuilder — fluent API for building workflows."""

    def test_build_empty_workflow(self) -> None:
        """An empty builder should produce a workflow with no steps."""
        from xruntime._runtime._workflow import WorkflowBuilder

        wf = WorkflowBuilder().id("wf-1").name("empty").build()
        assert isinstance(wf, Workflow)
        assert wf.id == "wf-1"
        assert wf.name == "empty"
        assert wf.steps == []

    def test_build_single_step(self) -> None:
        """A single step should be added correctly."""
        from xruntime._runtime._workflow import WorkflowBuilder

        wf = (
            WorkflowBuilder()
            .id("wf-1")
            .name("single")
            .step(id="s1", name="step-1", agent="agent-1", prompt="hi")
            .build()
        )
        assert len(wf.steps) == 1
        assert wf.steps[0].id == "s1"
        assert wf.steps[0].agent == "agent-1"
        assert wf.steps[0].prompt == "hi"

    def test_build_linear_workflow(self) -> None:
        """A 3-step linear workflow with dependencies."""
        from xruntime._runtime._workflow import WorkflowBuilder

        wf = (
            WorkflowBuilder()
            .id("wf-1")
            .name("linear")
            .step(id="s1", name="step-1", agent="a1", prompt="p1")
            .step(
                id="s2",
                name="step-2",
                agent="a2",
                prompt="p2",
                depends_on=["s1"],
            )
            .step(
                id="s3",
                name="step-3",
                agent="a3",
                prompt="p3",
                depends_on=["s2"],
            )
            .build()
        )
        assert len(wf.steps) == 3
        assert wf.steps[1].depends_on == ["s1"]
        assert wf.steps[2].depends_on == ["s2"]

    def test_step_default_name_is_id(self) -> None:
        """When name is omitted, it defaults to the id."""
        from xruntime._runtime._workflow import WorkflowBuilder

        wf = (
            WorkflowBuilder()
            .id("wf-1")
            .step(id="s1", agent="a1", prompt="p1")
            .build()
        )
        assert wf.steps[0].name == "s1"

    def test_step_default_failure_strategy_is_abort(self) -> None:
        """on_failure should default to 'abort'."""
        from xruntime._runtime._workflow import WorkflowBuilder

        wf = (
            WorkflowBuilder()
            .id("wf-1")
            .step(id="s1", agent="a1", prompt="p1")
            .build()
        )
        assert wf.steps[0].on_failure == "abort"

    def test_step_with_failure_strategy(self) -> None:
        """on_failure='continue' should be honored."""
        from xruntime._runtime._workflow import WorkflowBuilder

        wf = (
            WorkflowBuilder()
            .id("wf-1")
            .step(
                id="s1",
                agent="a1",
                prompt="p1",
                on_failure="continue",
            )
            .build()
        )
        assert wf.steps[0].on_failure == "continue"

    def test_builder_auto_generates_id_when_not_set(self) -> None:
        """When id() is not called, a unique id is auto-generated."""
        from xruntime._runtime._workflow import WorkflowBuilder

        wf = WorkflowBuilder().name("auto").build()
        assert wf.id  # non-empty
        assert wf.id.startswith("wf-")

    def test_builder_default_name_is_id(self) -> None:
        """When name() is not called, name defaults to id."""
        from xruntime._runtime._workflow import WorkflowBuilder

        wf = WorkflowBuilder().id("wf-x").build()
        assert wf.name == "wf-x"

    def test_builder_step_returns_builder_for_chaining(self) -> None:
        """step() should return self for fluent chaining."""
        from xruntime._runtime._workflow import WorkflowBuilder

        b = WorkflowBuilder().id("wf-1")
        assert b.step(id="s1", agent="a", prompt="p") is b

    def test_build_workflow_with_topological_order(self) -> None:
        """The built workflow's topological_order should work."""
        from xruntime._runtime._workflow import WorkflowBuilder

        wf = (
            WorkflowBuilder()
            .id("wf-1")
            .step(id="a", agent="x", prompt="p")
            .step(id="b", agent="x", prompt="p", depends_on=["a"])
            .step(id="c", agent="x", prompt="p", depends_on=["a"])
            .step(id="d", agent="x", prompt="p", depends_on=["b", "c"])
            .build()
        )
        order = wf.topological_order()
        assert order[0] == "a"
        assert order[-1] == "d"


# ── 2. FunctionExecutor ────────────────────────────────────────────


class TestFunctionExecutor:
    """FunctionExecutor — wraps a sync callable into an async executor."""

    async def test_basic_execution(self) -> None:
        """A simple sync function should be wrapped and awaited."""
        from xruntime._runtime._workflow import FunctionExecutor

        def fn(step: WorkflowStep, context: dict[str, Any]) -> str:
            return f"output-of-{step.id}"

        executor = FunctionExecutor(fn)
        step = WorkflowStep(id="s1", name="s1", agent="a", prompt="p")
        result = await executor(step, {})
        assert result == "output-of-s1"

    async def test_context_is_passed_through(self) -> None:
        """The context dict should be passed to the wrapped function."""
        from xruntime._runtime._workflow import FunctionExecutor

        def fn(step: WorkflowStep, context: dict[str, Any]) -> str:
            return context.get("s0", "missing")

        executor = FunctionExecutor(fn)
        step = WorkflowStep(id="s1", name="s1", agent="a", prompt="p")
        result = await executor(step, {"s0": "from-s0"})
        assert result == "from-s0"

    async def test_function_raising_returns_none(self) -> None:
        """A function that raises should yield None (Orchestrator semantics)."""
        from xruntime._runtime._workflow import FunctionExecutor

        def fn(step: WorkflowStep, context: dict[str, Any]) -> str:
            raise RuntimeError("boom")

        executor = FunctionExecutor(fn)
        step = WorkflowStep(id="s1", name="s1", agent="a", prompt="p")
        # Should not raise — FunctionExecutor catches and returns None
        result = await executor(step, {})
        assert result is None

    def test_is_step_executor(self) -> None:
        """FunctionExecutor should be callable as a StepExecutor."""
        from xruntime._runtime._workflow import FunctionExecutor

        executor = FunctionExecutor(lambda s, c: "")
        # StepExecutor is a Callable type; just verify it's callable
        assert callable(executor)


# ── 3. run_workflow ────────────────────────────────────────────────


class TestRunWorkflow:
    """run_workflow — high-level convenience runner."""

    async def test_run_without_store(self) -> None:
        """Running without a store should produce in-memory results."""
        from xruntime._runtime._workflow import (
            FunctionExecutor,
            run_workflow,
        )

        wf = Workflow(
            id="wf-1",
            name="test",
            steps=[
                WorkflowStep(id="s1", name="s1", agent="a", prompt="p"),
            ],
        )
        executor = FunctionExecutor(
            lambda step, ctx: f"out-{step.id}",
        )
        result = await run_workflow(wf, executor)
        assert isinstance(result, WorkflowResult)
        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["s1"] == "out-s1"

    async def test_run_with_store_persists_checkpoints(self) -> None:
        """Running with a store should persist a COMPLETED checkpoint."""
        from xruntime._runtime._workflow import (
            FunctionExecutor,
            InMemoryCheckpointStore,
            run_workflow,
        )

        wf = Workflow(
            id="wf-1",
            name="test",
            steps=[
                WorkflowStep(id="s1", name="s1", agent="a", prompt="p"),
                WorkflowStep(
                    id="s2",
                    name="s2",
                    agent="a",
                    prompt="p",
                    depends_on=["s1"],
                ),
            ],
        )
        store = InMemoryCheckpointStore()
        executor = FunctionExecutor(lambda step, ctx: f"out-{step.id}")
        result = await run_workflow(wf, executor, store=store)
        assert result.status == WorkflowStatus.COMPLETED
        # At least one COMPLETED checkpoint should exist
        latest = await store.latest_for_workflow("wf-1")
        assert latest is not None
        assert latest.status == "COMPLETED"

    async def test_run_with_ttl(self) -> None:
        """Passing ttl_seconds should set checkpoint expiry."""
        from xruntime._runtime._workflow import (
            FunctionExecutor,
            InMemoryCheckpointStore,
            run_workflow,
        )

        wf = Workflow(
            id="wf-1",
            name="test",
            steps=[WorkflowStep(id="s1", name="s1", agent="a", prompt="p")],
        )
        store = InMemoryCheckpointStore()
        executor = FunctionExecutor(lambda step, ctx: "ok")
        await run_workflow(wf, executor, store=store, ttl_seconds=3600)
        latest = await store.latest_for_workflow("wf-1")
        assert latest is not None
        assert latest.expires_at is not None


# ── 4. resume_workflow ─────────────────────────────────────────────


class TestResumeWorkflow:
    """resume_workflow — resume from latest checkpoint."""

    async def test_resume_returns_none_when_no_checkpoint(self) -> None:
        """Resume without any checkpoint should return None."""
        from xruntime._runtime._workflow import (
            FunctionExecutor,
            InMemoryCheckpointStore,
            resume_workflow,
        )

        wf = Workflow(
            id="wf-none",
            name="test",
            steps=[WorkflowStep(id="s1", name="s1", agent="a", prompt="p")],
        )
        store = InMemoryCheckpointStore()
        executor = FunctionExecutor(lambda step, ctx: "ok")
        result = await resume_workflow(wf, store=store, executor=executor)
        assert result is None

    async def test_resume_after_completion_returns_completed(self) -> None:
        """Resume after a completed workflow should return COMPLETED."""
        from xruntime._runtime._workflow import (
            FunctionExecutor,
            InMemoryCheckpointStore,
            resume_workflow,
            run_workflow,
        )

        wf = Workflow(
            id="wf-1",
            name="test",
            steps=[
                WorkflowStep(id="s1", name="s1", agent="a", prompt="p"),
            ],
        )
        store = InMemoryCheckpointStore()
        executor = FunctionExecutor(lambda step, ctx: "out")
        await run_workflow(wf, executor, store=store)
        result = await resume_workflow(wf, store=store, executor=executor)
        assert result is not None
        assert result.status == WorkflowStatus.COMPLETED

    async def test_resume_skips_completed_steps(self) -> None:
        """Resume should not re-run completed steps."""
        from xruntime._runtime._workflow import (
            FunctionExecutor,
            InMemoryCheckpointStore,
            resume_workflow,
            run_workflow,
        )

        wf = Workflow(
            id="wf-1",
            name="test",
            steps=[
                WorkflowStep(id="s1", name="s1", agent="a", prompt="p"),
                WorkflowStep(
                    id="s2",
                    name="s2",
                    agent="a",
                    prompt="p",
                    depends_on=["s1"],
                ),
            ],
        )
        store = InMemoryCheckpointStore()
        call_count = {"n": 0}

        def fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            call_count["n"] += 1
            return f"out-{step.id}"

        executor = FunctionExecutor(fn)
        await run_workflow(wf, executor, store=store)
        first_calls = call_count["n"]
        # Resume should not re-execute any steps
        await resume_workflow(wf, store=store, executor=executor)
        assert call_count["n"] == first_calls

    async def test_resume_completed_without_executor(self) -> None:
        """Resume a completed workflow with no executor should work."""
        from xruntime._runtime._workflow import (
            FunctionExecutor,
            InMemoryCheckpointStore,
            resume_workflow,
            run_workflow,
        )

        wf = Workflow(
            id="wf-1",
            name="test",
            steps=[WorkflowStep(id="s1", name="s1", agent="a", prompt="p")],
        )
        store = InMemoryCheckpointStore()
        await run_workflow(
            wf,
            FunctionExecutor(lambda s, c: "ok"),
            store=store,
        )
        # No executor — should still succeed because workflow is done
        result = await resume_workflow(wf, store=store)
        assert result is not None
        assert result.status == WorkflowStatus.COMPLETED

    async def test_resume_incomplete_without_executor_raises(self) -> None:
        """Resume an incomplete workflow without an executor raises."""
        from xruntime._runtime._workflow import (
            InMemoryCheckpointStore,
            resume_workflow,
        )
        from xruntime._runtime._workflow._checkpoint import Checkpoint

        wf = Workflow(
            id="wf-1",
            name="test",
            steps=[
                WorkflowStep(id="s1", name="s1", agent="a", prompt="p"),
                WorkflowStep(
                    id="s2",
                    name="s2",
                    agent="a",
                    prompt="p",
                    depends_on=["s1"],
                ),
            ],
        )
        store = InMemoryCheckpointStore()
        # Manually save an ACTIVE (in-progress) checkpoint
        cp = Checkpoint(
            workflow_id="wf-1",
            workflow_name="test",
            step_id="s1",
            step_name="s1",
            step_results={"s1": "out-s1"},
            step_status={"s1": "COMPLETED", "s2": "PENDING"},
            status="ACTIVE",
        )
        await store.save(cp)
        # No executor + incomplete workflow → ValueError
        with pytest.raises(ValueError, match="executor is required"):
            await resume_workflow(wf, store=store)


# ── 5. load_workflow_from_file ─────────────────────────────────────


class TestLoadWorkflowFromFile:
    """load_workflow_from_file — YAML loader from disk."""

    def test_load_valid_yaml(self) -> None:
        """A valid YAML file should load into a Workflow."""
        from xruntime._runtime._workflow import load_workflow_from_file

        yaml_content = """
id: wf-from-yaml
name: From YAML
steps:
  - id: s1
    name: step-1
    agent: agent-1
    prompt: "do something"
  - id: s2
    agent: agent-2
    prompt: "do more"
    depends_on: [s1]
"""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
        ) as f:
            f.write(yaml_content)
            f.flush()
            path = f.name
        try:
            wf = load_workflow_from_file(path)
            assert wf.id == "wf-from-yaml"
            assert wf.name == "From YAML"
            assert len(wf.steps) == 2
            assert wf.steps[0].id == "s1"
            assert wf.steps[1].depends_on == ["s1"]
        finally:
            os.unlink(path)

    def test_load_missing_file_raises(self) -> None:
        """A missing file should raise FileNotFoundError."""
        from xruntime._runtime._workflow import load_workflow_from_file

        with pytest.raises(FileNotFoundError):
            load_workflow_from_file("/nonexistent/path/to/file.yaml")

    def test_load_invalid_yaml_raises(self) -> None:
        """Invalid YAML should raise a ValueError or YAMLError."""
        from xruntime._runtime._workflow import load_workflow_from_file

        yaml_content = "not: valid: yaml: ["
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
        ) as f:
            f.write(yaml_content)
            f.flush()
            path = f.name
        try:
            with pytest.raises(Exception):
                load_workflow_from_file(path)
        finally:
            os.unlink(path)

    def test_load_yaml_missing_id_raises(self) -> None:
        """A YAML without an id field should raise ValueError."""
        from xruntime._runtime._workflow import load_workflow_from_file

        yaml_content = """
name: no id
steps: []
"""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
        ) as f:
            f.write(yaml_content)
            f.flush()
            path = f.name
        try:
            with pytest.raises(ValueError):
                load_workflow_from_file(path)
        finally:
            os.unlink(path)


# ── 6. Public exports ──────────────────────────────────────────────


class TestPublicExports:
    """The _workflow package should re-export the SDK surface."""

    def test_workflow_builder_exported(self) -> None:
        """WorkflowBuilder should be importable from the package."""
        from xruntime._runtime._workflow import WorkflowBuilder

        assert WorkflowBuilder is not None

    def test_function_executor_exported(self) -> None:
        """FunctionExecutor should be importable from the package."""
        from xruntime._runtime._workflow import FunctionExecutor

        assert FunctionExecutor is not None

    def test_run_workflow_exported(self) -> None:
        """run_workflow should be importable from the package."""
        from xruntime._runtime._workflow import run_workflow

        assert callable(run_workflow)

    def test_resume_workflow_exported(self) -> None:
        """resume_workflow should be importable from the package."""
        from xruntime._runtime._workflow import resume_workflow

        assert callable(resume_workflow)

    def test_load_workflow_from_file_exported(self) -> None:
        """load_workflow_from_file should be importable."""
        from xruntime._runtime._workflow import load_workflow_from_file

        assert callable(load_workflow_from_file)

    def test_all_expected_names_in_all(self) -> None:
        """__all__ should list all SDK names."""
        from xruntime._runtime import _workflow

        expected = {
            "Checkpoint",
            "CheckpointStatus",
            "CheckpointStore",
            "InMemoryCheckpointStore",
            "CheckpointedOrchestrator",
            "WorkflowConfig",
            "WorkflowBuilder",
            "FunctionExecutor",
            "run_workflow",
            "resume_workflow",
            "load_workflow_from_file",
        }
        assert expected.issubset(set(_workflow.__all__))
