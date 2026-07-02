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
import time
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
        self._tracer: Any = None  # set by run_workflow(tracer=...)

    async def run(
        self,
        workflow: Workflow,
    ) -> WorkflowResult:
        """Execute a workflow.

        Steps without dependencies on each other run in parallel
        within each topological layer.

        When ``self._tracer`` is set, the entire run is wrapped in a
        root span ``workflow.run`` and each step in a child span
        ``workflow.step.<id>`` with attributes and events.

        Args:
            workflow (`Workflow`):
                The workflow to execute.

        Returns:
            `WorkflowResult`: The execution result.
        """
        tracer = getattr(self, "_tracer", None)
        if tracer is not None:
            root_span = tracer.start_workflow_span(
                workflow.id,
                workflow.name,
            )
            root_span.__enter__()
        else:
            root_span = None

        try:
            result = await self._run_inner(workflow)
        finally:
            if root_span is not None and tracer is not None:
                if result.status == WorkflowStatus.COMPLETED:
                    root_span.set_attribute(
                        "workflow.status",
                        "COMPLETED",
                    )
                else:
                    root_span.set_attribute(
                        "workflow.status",
                        str(result.status),
                    )
                root_span.__exit__(None, None, None)
        return result

    async def _run_inner(
        self,
        workflow: Workflow,
    ) -> WorkflowResult:
        """Inner execution logic (called by :meth:`run`)."""
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
                self._execute_step_with_tracer(
                    step,
                    dict(context),
                    result,
                    workflow.id,
                )
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

    async def _execute_step_with_tracer(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
        result: WorkflowResult,
        workflow_id: str,
    ) -> str | None:
        """Execute a step with optional tracing and metrics.

        Wraps the step in a tracer span (if ``self._tracer`` is set)
        and records metrics (if ``self._metrics`` is set).
        """
        tracer = getattr(self, "_tracer", None)
        metrics = getattr(self, "_metrics", None)
        audit = getattr(self, "_audit", None)
        needs_timing = metrics is not None or audit is not None
        start_time = time.time() if needs_timing else None

        if tracer is None:
            output = await self._execute_step(step, context, result)
        else:
            span = tracer.start_step_span(
                step.id,
                step.agent,
                workflow_id,
            )
            span.__enter__()
            try:
                span.add_event("step.started")
                output = await self._execute_step(step, context, result)
                if output is None:
                    span.set_attribute("step.status", "FAILED")
                    span.add_event("step.failed")
                else:
                    span.set_attribute("step.status", "COMPLETED")
                    span.add_event("step.completed")
            except Exception as e:
                span.set_attribute("step.status", "FAILED")
                span.add_event("step.failed", error=str(e))
                raise
            finally:
                span.__exit__(None, None, None)

        # Record metrics (outside the span so timing includes
        # the span overhead too — matches Prometheus expectations).
        if metrics is not None and start_time is not None:
            duration_ms = (time.time() - start_time) * 1000
            status = "FAILED" if output is None else "COMPLETED"
            metrics.record_step(
                workflow_id=workflow_id,
                step_id=step.id,
                status=status,
            )
            metrics.record_step_duration(
                workflow_id=workflow_id,
                step_id=step.id,
                duration_ms=round(duration_ms, 2),
            )

        # Record audit entry (if audit logger is configured)
        if audit is not None and start_time is not None:
            audit_status = "FAILED" if output is None else "COMPLETED"
            audit_duration = (time.time() - start_time) * 1000
            audit.record_step(
                workflow_id=workflow_id,
                step_id=step.id,
                agent=step.agent,
                status=audit_status,
                duration_ms=round(audit_duration, 2),
                tenant_id=getattr(self, "_current_tenant_id", ""),
            )

        return output

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

        Extended step types (``ConditionalStep``, ``LoopStep``,
        ``SubWorkflowStep``, ``TimerStep``) are dispatched to their
        dedicated handlers via :meth:`_try_execute_extended_step`.
        The base :class:`WorkflowStep` goes through the standard
        retry + executor path.

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
        # Dispatch to extended step handlers (lazy import to avoid
        # circular dependency with the workflow subpackage).
        extended_output = await self._try_execute_extended_step(
            step,
            context,
            result,
        )
        if extended_output is not _NO_OUTPUT:
            return extended_output

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

    async def _try_execute_extended_step(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
        result: WorkflowResult,
    ) -> "str | None | _NoOutput":
        """Dispatch extended step types to their handlers.

        Returns :data:`_NO_OUTPUT` when the step is not an extended
        type (so the caller knows to fall through to the standard
        executor path).  Otherwise returns the step's output (or
        ``None`` on failure).

        Args:
            step (`WorkflowStep`): The step to dispatch.
            context (`dict[str, Any]`): The workflow context.
            result (`WorkflowResult`): The accumulating result.

        Returns:
            `str | None | _NoOutput`: The step output, ``None`` on
            failure, or :data:`_NO_OUTPUT` if the step is not an
            extended type.
        """
        try:
            from ._workflow._steps import (
                ConditionalStep,
                LoopStep,
                SubWorkflowStep,
                TimerStep,
            )
        except ImportError:
            return _NO_OUTPUT

        if isinstance(step, ConditionalStep):
            return await self._execute_conditional_step(
                step,
                context,
                result,
            )
        if isinstance(step, LoopStep):
            return await self._execute_loop_step(
                step,
                context,
                result,
            )
        if isinstance(step, SubWorkflowStep):
            return await self._execute_subworkflow_step(
                step,
                context,
                result,
            )
        if isinstance(step, TimerStep):
            return await self._execute_timer_step(
                step,
                context,
                result,
            )
        try:
            from ._workflow._approval import ApprovalStep
        except ImportError:
            ApprovalStep = None  # type: ignore[assignment]
        if ApprovalStep is not None and isinstance(step, ApprovalStep):
            return await self._execute_approval_step(
                step,
                context,
                result,
            )
        return _NO_OUTPUT

    async def _execute_conditional_step(
        self,
        step: "ConditionalStep",  # type: name  # noqa: F821
        context: dict[str, Any],
        result: WorkflowResult,
    ) -> str | None:
        """Execute a :class:`ConditionalStep`.

        Evaluates ``condition(context)``.  When ``True``, runs the
        inner steps in sequence (honouring their ``depends_on``)
        and returns the last inner step's output.  When ``False``,
        returns an empty string (the branch is "executed" but
        produces no output).

        Args:
            step (`ConditionalStep`): The conditional step.
            context (`dict[str, Any]`): The workflow context.
            result (`WorkflowResult`): The accumulating result.

        Returns:
            `str | None`: The branch output (empty string when
            condition is ``False``), or ``None`` on inner-step
            failure.
        """
        if not step.eval_condition(context):
            return ""

        # Execute inner steps in sequence.  Inner steps share the
        # branch's context (a copy of the parent context) and each
        # inner step's output is added to that context so later
        # inner steps can reference it.
        branch_context = dict(context)
        last_output = ""
        for inner_step in step.inner_steps:
            try:
                output = await self._executor(inner_step, branch_context)
            except Exception as e:  # noqa: BLE001
                result.errors.append(f"{step.id}/{inner_step.id}: {e}")
                if step.on_failure == "abort":
                    return None
                output = ""
            if output is None:
                output = ""
            branch_context[inner_step.id] = output
            last_output = output
        return last_output

    async def _execute_loop_step(
        self,
        step: "LoopStep",  # type: name  # noqa: F821
        context: dict[str, Any],
        result: WorkflowResult,
    ) -> str | None:
        """Execute a :class:`LoopStep`.

        Iterates up to ``max_iterations`` times while ``condition``
        evaluates to ``True`` on the (mutating) context.  Each
        iteration's output is stored as ``context[step.id]`` so the
        next iteration can reference it.  Returns the last
        iteration's output, or an empty string when zero iterations
        run.

        When ``on_failure`` is ``"abort"`` (the default), an
        iteration exception causes the step to fail (returns
        ``None``).  When ``on_failure`` is ``"continue"`` or
        ``"retry"``, a failed iteration produces an empty output and
        the loop continues.

        Args:
            step (`LoopStep`): The loop step.
            context (`dict[str, Any]`): The workflow context.
            result (`WorkflowResult`): The accumulating result.

        Returns:
            `str | None`: The last iteration's output (empty string
            when zero iterations), or ``None`` on failure with
            ``on_failure="abort"``.
        """
        loop_context = dict(context)
        last_output = ""
        for _ in range(step.max_iterations):
            # Check condition *before* each iteration.
            try:
                should_run = bool(step.condition(loop_context))
            except Exception:  # noqa: BLE001
                # Condition raised — fail closed (exit loop).
                break
            if not should_run:
                break

            try:
                output = await self._executor(step, dict(loop_context))
            except Exception as e:  # noqa: BLE001
                result.errors.append(f"{step.id}: {e}")
                if step.on_failure == "abort":
                    return None
                # continue / retry-exhausted: treat as empty output
                output = ""

            if output is None:
                if step.on_failure == "abort":
                    return None
                output = ""

            loop_context[step.id] = output
            last_output = output

        return last_output

    async def _execute_subworkflow_step(
        self,
        step: "SubWorkflowStep",  # type: name  # noqa: F821
        context: dict[str, Any],
        result: WorkflowResult,
    ) -> str | None:
        """Execute a :class:`SubWorkflowStep`.

        Runs the sub-workflow's steps in topological order, sharing
        the parent context so sub-steps can reference parent outputs.
        Returns the last sub-step's output (or empty string when the
        sub-workflow has no steps).

        Sub-step failures follow the sub-step's own ``on_failure``
        strategy.  When ``on_failure="abort"`` and a sub-step fails,
        the entire sub-workflow step returns ``None`` (failure
        propagates to the parent).

        Args:
            step (`SubWorkflowStep`): The sub-workflow step.
            context (`dict[str, Any]`): The parent workflow context.
            result (`WorkflowResult`): The accumulating parent result.

        Returns:
            `str | None`: The sub-workflow's last step output, or
            ``None`` on failure.
        """
        sub_wf = step.sub_workflow
        if not sub_wf.steps:
            return ""

        sub_context = dict(context)
        order = sub_wf.topological_order()
        sub_step_map = {s.id: s for s in sub_wf.steps}

        last_output = ""
        for sub_step_id in order:
            sub_step = sub_step_map[sub_step_id]
            # Check deps within sub-workflow
            deps_failed = any(
                sub_context.get(dep) == ""
                and sub_step_map[dep].on_failure == "abort"
                for dep in sub_step.depends_on
                if dep in sub_step_map
            )
            if deps_failed and sub_step.on_failure == "abort":
                # Skip this sub-step
                continue

            try:
                output = await self._executor(sub_step, dict(sub_context))
            except Exception as e:  # noqa: BLE001
                result.errors.append(
                    f"{step.id}/{sub_step.id}: {e}",
                )
                if sub_step.on_failure == "abort":
                    return None
                output = ""

            if output is None:
                if sub_step.on_failure == "abort":
                    return None
                output = ""

            sub_context[sub_step.id] = output
            last_output = output

        return last_output

    async def _execute_timer_step(
        self,
        step: "TimerStep",  # type: name  # noqa: F821
        context: dict[str, Any],
        result: WorkflowResult,
    ) -> str | None:
        """Execute a :class:`TimerStep`.

        Sleeps for ``duration_seconds`` (using :func:`asyncio.sleep`)
        and returns an empty string.  When ``duration_seconds <= 0``,
        the timer is a no-op (returns immediately).

        In checkpoint mode, the :class:`CheckpointedOrchestrator`
        saves a ``SLEEPING`` checkpoint before sleeping so a crashed
        workflow can be resumed after the timer elapses.  This base
        implementation does the simple in-memory sleep; the
        checkpoint behavior is layered on by the
        :class:`CheckpointedOrchestrator` override.

        Args:
            step (`TimerStep`): The timer step.
            context (`dict[str, Any]`): The workflow context.
            result (`WorkflowResult`): The accumulating result.

        Returns:
            `str`: Always an empty string (timers produce no data).
        """
        duration = step.duration_seconds
        if duration > 0:
            await asyncio.sleep(duration)
        return ""

    async def _execute_approval_step(
        self,
        step: "ApprovalStep",  # type: name  # noqa: F821
        context: dict[str, Any],
        result: WorkflowResult,
    ) -> str | None:
        """Execute an :class:`ApprovalStep`.

        Creates an approval request in the orchestrator's
        :class:`ApprovalStore` (if configured) and returns an empty
        string (the approval step itself produces no data; downstream
        steps check the approval status via the store).

        When no approval store is configured, the step auto-approves
        (useful for tests / dev — fail-open so workflows don't hang).

        Args:
            step (`ApprovalStep`): The approval step.
            context (`dict[str, Any]`): The workflow context.
            result (`WorkflowResult`): The accumulating result.

        Returns:
            `str`: Always an empty string (approval status lives in
            the store, not in the step output).
        """
        store = getattr(self, "_approval_store", None)
        if store is None:
            # No store configured — auto-approve (dev / test mode)
            return ""
        # Create approval request
        await store.create_request(
            workflow_id=getattr(self, "_current_workflow_id", ""),
            step_id=step.id,
            approver=step.approver,
            timeout_seconds=step.timeout_seconds,
        )
        # In a real orchestrator, the workflow would pause here and
        # resume on decision.  For the base Orchestrator (non-
        # checkpoint), we return empty — the CheckpointedOrchestrator
        # override handles the pause/resume lifecycle.
        return ""


class _NoOutput:
    """Sentinel type indicating no extended handler matched.

    Used as the return value of :meth:`_try_execute_extended_step`
    when the step is not an extended type.  A dedicated sentinel
    class (rather than ``None``) is needed because ``None`` is a
    valid step-failure return value.
    """


_NO_OUTPUT = _NoOutput()
