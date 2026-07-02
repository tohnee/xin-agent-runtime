# -*- coding: utf-8 -*-
"""Tests for the Orchestrator — DAG workflow engine."""
import asyncio
from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    WorkflowStep,
    Workflow,
    WorkflowStatus,
    Orchestrator,
    StepStatus,
    parse_workflow_yaml,
)


class TestWorkflowStep:
    """Tests for WorkflowStep model."""

    def test_creation(self) -> None:
        """Step should capture id/name/agent/prompt/deps."""
        step = WorkflowStep(
            id="analyze",
            name="Analyze codebase",
            agent="code-analyzer",
            prompt="Find all TODO comments",
            depends_on=[],
        )
        assert step.id == "analyze"
        assert step.agent == "code-analyzer"
        assert step.depends_on == []

    def test_with_dependencies(self) -> None:
        """Step with deps should record them."""
        step = WorkflowStep(
            id="fix",
            name="Fix bugs",
            agent="coder",
            prompt="Fix the bugs found",
            depends_on=["analyze"],
        )
        assert step.depends_on == ["analyze"]

    def test_with_failure_strategy(self) -> None:
        """Step should support failure strategy."""
        step = WorkflowStep(
            id="deploy",
            name="Deploy",
            agent="deployer",
            prompt="Deploy the app",
            depends_on=["fix"],
            on_failure="retry",
            max_retries=3,
        )
        assert step.on_failure == "retry"
        assert step.max_retries == 3

    def test_default_failure_strategy(self) -> None:
        """Default failure strategy should be abort."""
        step = WorkflowStep(
            id="s1",
            name="Step 1",
            agent="a1",
            prompt="Do something",
        )
        assert step.on_failure == "abort"
        assert step.max_retries == 0


class TestWorkflow:
    """Tests for Workflow model."""

    def test_creation(self) -> None:
        """Workflow should capture steps."""
        wf = Workflow(
            id="wf-1",
            name="Code review workflow",
            steps=[
                WorkflowStep(
                    id="analyze",
                    name="Analyze",
                    agent="analyzer",
                    prompt="Analyze code",
                ),
                WorkflowStep(
                    id="report",
                    name="Report",
                    agent="reporter",
                    prompt="Write report",
                    depends_on=["analyze"],
                ),
            ],
        )
        assert wf.id == "wf-1"
        assert len(wf.steps) == 2

    def test_get_step(self) -> None:
        """Should get a step by id."""
        wf = Workflow(
            id="wf-1",
            name="Test",
            steps=[
                WorkflowStep(
                    id="s1",
                    name="S1",
                    agent="a",
                    prompt="p",
                ),
            ],
        )
        assert wf.get_step("s1") is not None
        assert wf.get_step("nonexistent") is None

    def test_topological_order(self) -> None:
        """Should produce topological order respecting deps."""
        wf = Workflow(
            id="wf-1",
            name="Pipeline",
            steps=[
                WorkflowStep(
                    id="c",
                    name="C",
                    agent="a",
                    prompt="p",
                    depends_on=["a", "b"],
                ),
                WorkflowStep(
                    id="a",
                    name="A",
                    agent="a",
                    prompt="p",
                ),
                WorkflowStep(
                    id="b",
                    name="B",
                    agent="a",
                    prompt="p",
                    depends_on=["a"],
                ),
            ],
        )
        order = wf.topological_order()
        assert order[0] == "a"
        assert order[1] == "b"
        assert order[2] == "c"

    def test_cycle_detection(self) -> None:
        """Circular dependencies should be detected."""
        wf = Workflow(
            id="wf-1",
            name="Cyclic",
            steps=[
                WorkflowStep(
                    id="a",
                    name="A",
                    agent="x",
                    prompt="p",
                    depends_on=["b"],
                ),
                WorkflowStep(
                    id="b",
                    name="B",
                    agent="x",
                    prompt="p",
                    depends_on=["a"],
                ),
            ],
        )
        with pytest.raises(ValueError, match="cycle"):
            wf.topological_order()

    def test_missing_dependency(self) -> None:
        """Missing dependency should raise."""
        wf = Workflow(
            id="wf-1",
            name="Bad deps",
            steps=[
                WorkflowStep(
                    id="s1",
                    name="S1",
                    agent="a",
                    prompt="p",
                    depends_on=["nonexistent"],
                ),
            ],
        )
        with pytest.raises(ValueError, match="nonexistent"):
            wf.topological_order()


class TestParseWorkflowYaml:
    """Tests for YAML workflow parsing."""

    def test_parse_simple(self) -> None:
        """Simple YAML should parse into Workflow."""
        yaml_str = """
id: wf-1
name: Test workflow
steps:
  - id: analyze
    name: Analyze
    agent: analyzer
    prompt: Find bugs
  - id: fix
    name: Fix
    agent: coder
    prompt: Fix bugs
    depends_on: [analyze]
"""
        wf = parse_workflow_yaml(yaml_str)
        assert wf.id == "wf-1"
        assert wf.name == "Test workflow"
        assert len(wf.steps) == 2
        assert wf.steps[1].depends_on == ["analyze"]

    def test_parse_with_failure_strategy(self) -> None:
        """YAML with failure strategy should parse."""
        yaml_str = """
id: wf-2
name: Retry workflow
steps:
  - id: deploy
    name: Deploy
    agent: deployer
    prompt: Deploy app
    on_failure: retry
    max_retries: 3
"""
        wf = parse_workflow_yaml(yaml_str)
        assert wf.steps[0].on_failure == "retry"
        assert wf.steps[0].max_retries == 3

    def test_parse_invalid_yaml(self) -> None:
        """Invalid YAML should raise."""
        with pytest.raises(Exception):
            parse_workflow_yaml("not: valid: yaml: :::")

    def test_parse_missing_id(self) -> None:
        """Workflow without id should raise."""
        yaml_str = """
name: No ID
steps:
  - id: s1
    name: S1
    agent: a
    prompt: p
"""
        with pytest.raises(ValueError, match="id"):
            parse_workflow_yaml(yaml_str)


class TestOrchestrator:
    """Tests for the Orchestrator engine."""

    def test_creation(self) -> None:
        """Orchestrator should be creatable."""
        orch = Orchestrator(executor=lambda *a: asyncio.sleep(0))
        assert orch is not None

    async def test_run_simple_workflow(self) -> None:
        """Simple 2-step workflow should execute in order."""
        executed: list[str] = []

        async def mock_executor(
            step: WorkflowStep,
            context: dict[str, Any],
        ) -> str:
            executed.append(step.id)
            return f"result-{step.id}"

        orch = Orchestrator(executor=mock_executor)
        wf = Workflow(
            id="wf-1",
            name="Simple",
            steps=[
                WorkflowStep(
                    id="s1",
                    name="S1",
                    agent="a",
                    prompt="p1",
                ),
                WorkflowStep(
                    id="s2",
                    name="S2",
                    agent="a",
                    prompt="p2",
                    depends_on=["s1"],
                ),
            ],
        )
        result = await orch.run(wf)
        assert result.status == WorkflowStatus.COMPLETED
        assert executed == ["s1", "s2"]
        assert result.step_results["s1"] == "result-s1"
        assert result.step_results["s2"] == "result-s2"

    async def test_parallel_steps(self) -> None:
        """Independent steps should run in parallel."""
        order: list[str] = []

        async def mock_executor(
            step: WorkflowStep,
            context: dict[str, Any],
        ) -> str:
            order.append(f"start-{step.id}")
            await asyncio.sleep(0.05)
            order.append(f"end-{step.id}")
            return step.id

        orch = Orchestrator(executor=mock_executor)
        wf = Workflow(
            id="wf-parallel",
            name="Parallel",
            steps=[
                WorkflowStep(
                    id="a",
                    name="A",
                    agent="x",
                    prompt="p",
                ),
                WorkflowStep(
                    id="b",
                    name="B",
                    agent="x",
                    prompt="p",
                ),
            ],
        )
        result = await orch.run(wf)
        assert result.status == WorkflowStatus.COMPLETED
        # Both should start before either ends
        assert order.index("start-a") < order.index("end-b")
        assert order.index("start-b") < order.index("end-a")

    async def test_failure_aborts(self) -> None:
        """Failure with abort strategy should stop workflow."""

        async def failing_executor(
            step: WorkflowStep,
            context: dict[str, Any],
        ) -> str:
            if step.id == "s1":
                raise RuntimeError("boom")
            return step.id

        orch = Orchestrator(executor=failing_executor)
        wf = Workflow(
            id="wf-fail",
            name="Failing",
            steps=[
                WorkflowStep(
                    id="s1",
                    name="S1",
                    agent="a",
                    prompt="p",
                ),
                WorkflowStep(
                    id="s2",
                    name="S2",
                    agent="a",
                    prompt="p",
                    depends_on=["s1"],
                ),
            ],
        )
        result = await orch.run(wf)
        assert result.status == WorkflowStatus.FAILED
        assert result.step_status["s1"] == StepStatus.FAILED
        assert "s2" not in result.step_results

    async def test_retry_on_failure(self) -> None:
        """Retry strategy should retry failed steps."""
        call_count: dict[str, int] = {"s1": 0}

        async def flaky_executor(
            step: WorkflowStep,
            context: dict[str, Any],
        ) -> str:
            call_count[step.id] = call_count.get(step.id, 0) + 1
            if call_count[step.id] < 3:
                raise RuntimeError("transient")
            return "success"

        orch = Orchestrator(executor=flaky_executor)
        wf = Workflow(
            id="wf-retry",
            name="Retry",
            steps=[
                WorkflowStep(
                    id="s1",
                    name="S1",
                    agent="a",
                    prompt="p",
                    on_failure="retry",
                    max_retries=3,
                ),
            ],
        )
        result = await orch.run(wf)
        assert result.status == WorkflowStatus.COMPLETED
        assert call_count["s1"] == 3

    async def test_context_passing(self) -> None:
        """Step results should be available to dependent steps."""

        async def mock_executor(
            step: WorkflowStep,
            context: dict[str, Any],
        ) -> str:
            if step.id == "s2":
                return f"got: {context.get('s1', 'none')}"
            return f"result-{step.id}"

        orch = Orchestrator(executor=mock_executor)
        wf = Workflow(
            id="wf-ctx",
            name="Context",
            steps=[
                WorkflowStep(
                    id="s1",
                    name="S1",
                    agent="a",
                    prompt="p",
                ),
                WorkflowStep(
                    id="s2",
                    name="S2",
                    agent="a",
                    prompt="p",
                    depends_on=["s1"],
                ),
            ],
        )
        result = await orch.run(wf)
        assert result.step_results["s2"] == "got: result-s1"
