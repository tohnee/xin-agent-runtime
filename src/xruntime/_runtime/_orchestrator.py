# -*- coding: utf-8 -*-
"""Orchestrator — declarative DAG workflow engine.

AS provides implicit orchestration (agents self-organize via
``TeamSay`` tools).  XRuntime adds explicit orchestration: a
declarative YAML workflow (DAG) that schedules multiple agent
sessions, passes results between steps, and handles failures
(retry / abort / human escalation).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable


class WorkflowStatus(StrEnum):
    """Workflow execution status.

    Values:
        PENDING: Not yet started.
        RUNNING: Currently executing.
        COMPLETED: All steps succeeded.
        FAILED: A step failed (abort strategy).
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StepStatus(StrEnum):
    """Individual step execution status.

    Values:
        PENDING: Waiting for dependencies.
        RUNNING: Currently executing.
        COMPLETED: Finished successfully.
        FAILED: Failed (and not retried successfully).
        SKIPPED: Skipped due to upstream failure.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class WorkflowStep:
    """A single step in a workflow DAG.

    Args:
        id (`str`):
            Unique step identifier (referenced by ``depends_on``).
        name (`str`):
            Human-readable step name.
        agent (`str`):
            Agent name to invoke for this step.
        prompt (`str`):
            The prompt to send to the agent.
        depends_on (`list[str]`):
            Step ids that must complete before this step runs.
        on_failure (`str`):
            Failure strategy — ``"abort"`` (default), ``"retry"``,
            or ``"continue"``.
        max_retries (`int`):
            Max retry attempts when ``on_failure="retry"``.
    """

    id: str
    name: str
    agent: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    on_failure: str = "abort"
    max_retries: int = 0


@dataclass
class Workflow:
    """A declarative workflow (DAG of :class:`WorkflowStep`).

    Args:
        id (`str`):
            Unique workflow identifier.
        name (`str`):
            Human-readable workflow name.
        steps (`list[WorkflowStep]`):
            The steps in this workflow.
    """

    id: str
    name: str
    steps: list[WorkflowStep] = field(default_factory=list)

    def get_step(self, step_id: str) -> WorkflowStep | None:
        """Look up a step by id.

        Args:
            step_id (`str`):
                The step id to find.

        Returns:
            `WorkflowStep | None`: The step, or ``None`` if not found.
        """
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def topological_order(self) -> list[str]:
        """Return step ids in topological order (deps first).

        Raises:
            `ValueError`: If a dependency is missing or a cycle is
                detected.

        Returns:
            `list[str]`: Step ids in execution order.
        """
        in_degree: dict[str, int] = {}
        adj: dict[str, list[str]] = {}

        for step in self.steps:
            in_degree.setdefault(step.id, 0)
            adj.setdefault(step.id, [])

        for step in self.steps:
            for dep in step.depends_on:
                if dep not in in_degree:
                    raise ValueError(
                        f"Step '{step.id}' depends on "
                        f"nonexistent step '{dep}'",
                    )
                adj[dep].append(step.id)
                in_degree[step.id] = in_degree.get(step.id, 0) + 1

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        order: list[str] = []
        processed = 0

        while queue:
            current = queue.pop(0)
            order.append(current)
            processed += 1
            for neighbor in adj[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if processed != len(in_degree):
            raise ValueError(
                "Workflow has a dependency cycle",
            )

        return order


@dataclass
class WorkflowResult:
    """Result of a workflow execution.

    Args:
        status (`WorkflowStatus`):
            Final workflow status.
        step_results (`dict[str, str]`):
            Output of each completed step, keyed by step id.
        step_status (`dict[str, StepStatus]`):
            Status of each step.
        errors (`list[str]`):
            Error messages for failed steps.
    """

    status: WorkflowStatus = WorkflowStatus.PENDING
    step_results: dict[str, str] = field(default_factory=dict)
    step_status: dict[str, StepStatus] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def parse_workflow_yaml(yaml_str: str) -> Workflow:
    """Parse a YAML workflow definition.

    Args:
        yaml_str (`str`):
            YAML string with ``id``, ``name``, and ``steps`` keys.

    Returns:
        `Workflow`: The parsed workflow.

    Raises:
        `ValueError`: If required fields are missing.
        `yaml.YAMLError`: If the YAML is invalid.
    """
    import yaml

    raw = yaml.safe_load(yaml_str)
    if not raw:
        raise ValueError("Empty workflow YAML")

    if "id" not in raw:
        raise ValueError("Workflow must have an 'id' field")

    steps: list[WorkflowStep] = []
    for step_raw in raw.get("steps", []):
        steps.append(
            WorkflowStep(
                id=step_raw["id"],
                name=step_raw.get("name", step_raw["id"]),
                agent=step_raw["agent"],
                prompt=step_raw["prompt"],
                depends_on=step_raw.get("depends_on", []),
                on_failure=step_raw.get("on_failure", "abort"),
                max_retries=step_raw.get("max_retries", 0),
            ),
        )

    return Workflow(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        steps=steps,
    )


# Executor function type: (step, context) -> result string
StepExecutor = Callable[
    [WorkflowStep, dict[str, Any]],
    Awaitable[str],
]


class Orchestrator:
    """DAG workflow execution engine.

    Executes :class:`Workflow` instances by running steps in
    topological order, passing results between dependent steps,
    and handling failures according to each step's ``on_failure``
    strategy.

    Args:
        executor (`StepExecutor`):
            Async callable ``(step, context) -> result`` that
            executes a single step.  In production, this drives
            an agent session via ``ChatService``.
    """

    def __init__(self, executor: StepExecutor) -> None:
        """Initialize the orchestrator."""
        self._executor = executor

    async def run(
        self,
        workflow: Workflow,
    ) -> WorkflowResult:
        """Execute a workflow.

        Steps without dependencies on each other run in parallel
        within each topological layer.

        Args:
            workflow (`Workflow`):
                The workflow to execute.

        Returns:
            `WorkflowResult`: The execution result.
        """
        result = WorkflowResult(status=WorkflowStatus.RUNNING)
        order = workflow.topological_order()
        step_map = {s.id: s for s in workflow.steps}

        for step_id in order:
            result.step_status[step_id] = StepStatus.PENDING

        context: dict[str, Any] = {}

        # Group steps into layers for parallel execution
        layers = self._group_into_layers(workflow, order)

        for layer in layers:
            # Check which steps in this layer can run. A step is skipped
            # only if a dependency was SKIPPED or FAILED with the
            # "abort" strategy — a dependency that FAILED with
            # "continue"/"retry" does NOT block dependents (they run
            # with that dep's output absent from the context).
            runnable: list[WorkflowStep] = []
            for step_id in layer:
                step = step_map[step_id]
                deps_failed = any(
                    result.step_status.get(dep) == StepStatus.SKIPPED
                    or (
                        result.step_status.get(dep) == StepStatus.FAILED
                        and step_map[dep].on_failure == "abort"
                    )
                    for dep in step.depends_on
                )
                if deps_failed:
                    result.step_status[step_id] = StepStatus.SKIPPED
                else:
                    result.step_status[step_id] = StepStatus.RUNNING
                    runnable.append(step)

            if not runnable:
                continue

            # Execute all runnable steps in this layer concurrently
            tasks = [
                self._execute_step(step, dict(context), result)
                for step in runnable
            ]
            outputs = await asyncio.gather(*tasks)

            # Process every same-layer result before deciding to abort,
            # so sibling outputs are not lost and no step is left in
            # RUNNING.
            abort = False
            for step, output in zip(runnable, outputs):
                if output is None:
                    result.step_status[step.id] = StepStatus.FAILED
                    if step.on_failure == "abort":
                        result.status = WorkflowStatus.FAILED
                        abort = True
                    else:
                        # "continue" / retry-exhausted: record an empty
                        # output so dependents can run; the workflow is
                        # not failed by this step alone.
                        context[step.id] = ""
                else:
                    result.step_status[step.id] = StepStatus.COMPLETED
                    result.step_results[step.id] = output
                    context[step.id] = output

            if abort:
                # Mark every not-yet-run step as skipped and stop.
                for remaining_id in order:
                    if (
                        remaining_id not in result.step_status
                        or result.step_status[remaining_id]
                        == StepStatus.PENDING
                    ):
                        result.step_status[remaining_id] = StepStatus.SKIPPED
                return result

        if result.status != WorkflowStatus.FAILED:
            result.status = WorkflowStatus.COMPLETED

        return result

    def _group_into_layers(
        self,
        workflow: Workflow,
        order: list[str],
    ) -> list[list[str]]:
        """Group step ids into parallel execution layers.

        Args:
            workflow (`Workflow`):
                The workflow.
            order (`list[str]`):
                Topological order of step ids.

        Returns:
            `list[list[str]]`: Layers of step ids, each layer
            can execute concurrently.
        """
        step_map = {s.id: s for s in workflow.steps}
        layers: list[list[str]] = []
        assigned: set[str] = set()

        for step_id in order:
            step = step_map[step_id]
            deps = set(step.depends_on)

            # Find the earliest layer where all deps are satisfied
            min_layer = 0
            for i, layer in enumerate(layers):
                if deps & set(layer):
                    min_layer = i + 1

            while len(layers) <= min_layer:
                layers.append([])

            if deps.issubset(assigned) or not deps:
                layers[min_layer].append(step_id)
                assigned.add(step_id)

        return [layer for layer in layers if layer]

    async def _execute_step(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
        result: WorkflowResult,
    ) -> str | None:
        """Execute a single step with retry logic.

        Args:
            step (`WorkflowStep`):
                The step to execute.
            context (`dict`):
                Results from completed steps.
            result (`WorkflowResult`):
                The accumulating result (for error logging).

        Returns:
            `str | None`: Step result, or ``None`` on failure.
        """
        max_attempts = (
            step.max_retries + 1 if step.on_failure == "retry" else 1
        )

        for attempt in range(max_attempts):
            try:
                return await self._executor(step, dict(context))
            except Exception as e:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0.01 * (attempt + 1))
                    continue
                result.errors.append(
                    f"{step.id}: {e}",
                )
                return None

        return None
