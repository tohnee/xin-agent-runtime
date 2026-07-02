# -*- coding: utf-8 -*-
"""Workflow SDK — high-level public API for building and running
durable workflows.

This module sits on top of the low-level
:class:`Orchestrator` + :class:`CheckpointedOrchestrator` primitives
and provides a fluent builder, a sync-callable executor wrapper, and
convenience functions for one-shot runs and resume-from-checkpoint.

Public surface:

* :class:`WorkflowBuilder` — fluent builder for :class:`Workflow`.
* :class:`FunctionExecutor` — wraps a sync ``(step, ctx) -> str``
  callable into an async :class:`StepExecutor`.
* :func:`run_workflow` — run a workflow with optional checkpointing.
* :func:`resume_workflow` — resume a checkpointed workflow.
* :func:`load_workflow_from_file` — load a YAML workflow from disk.

Typical usage::

    from xruntime._runtime._workflow import (
        WorkflowBuilder, FunctionExecutor, run_workflow,
    )

    wf = (
        WorkflowBuilder()
        .id("my-wf")
        .step(id="s1", agent="coder", prompt="write tests")
        .step(id="s2", agent="reviewer", prompt="review",
              depends_on=["s1"])
        .build()
    )

    async def my_step(step, ctx):
        return f"ran-{step.id}"

    executor = FunctionExecutor(my_step)
    result = await run_workflow(wf, executor)
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from .._orchestrator import (
    Orchestrator,
    StepExecutor,
    Workflow,
    WorkflowResult,
    WorkflowStep,
    parse_workflow_yaml,
)
from ._checkpoint import CheckpointStore
from ._orchestrator import CheckpointedOrchestrator


# ── WorkflowBuilder ────────────────────────────────────────────────


class WorkflowBuilder:
    """Fluent builder for :class:`Workflow` instances.

    Provides a chainable API for constructing workflows
    programmatically without writing YAML.  All methods return
    ``self`` (except :meth:`build`) so calls can be chained.

    Usage::

        wf = (
            WorkflowBuilder()
            .id("my-wf")
            .name("My Workflow")
            .step(id="s1", agent="a", prompt="p")
            .step(id="s2", agent="a", prompt="p", depends_on=["s1"])
            .build()
        )
    """

    def __init__(self) -> None:
        """Initialize an empty builder."""
        self._id: str | None = None
        self._name: str | None = None
        self._steps: list[WorkflowStep] = []

    def id(self, workflow_id: str) -> "WorkflowBuilder":
        """Set the workflow id.

        Args:
            workflow_id (`str`): The workflow id.

        Returns:
            `WorkflowBuilder`: self, for chaining.
        """
        self._id = workflow_id
        return self

    def name(self, name: str) -> "WorkflowBuilder":
        """Set the workflow name.

        Args:
            name (`str`): The workflow name.

        Returns:
            `WorkflowBuilder`: self, for chaining.
        """
        self._name = name
        return self

    def step(
        self,
        *,
        id: str,
        agent: str,
        prompt: str,
        name: str | None = None,
        depends_on: list[str] | None = None,
        on_failure: str = "abort",
        max_retries: int = 0,
    ) -> "WorkflowBuilder":
        """Add a step to the workflow.

        Args:
            id (`str`): Unique step identifier.
            agent (`str`): Agent name to invoke.
            prompt (`str`): The prompt to send to the agent.
            name (`str | None`):
                Human-readable step name.  Defaults to ``id``.
            depends_on (`list[str] | None`):
                Step ids that must complete first.
            on_failure (`str`):
                Failure strategy — ``"abort"`` (default),
                ``"retry"``, or ``"continue"``.
            max_retries (`int`):
                Max retries when ``on_failure="retry"``.

        Returns:
            `WorkflowBuilder`: self, for chaining.
        """
        self._steps.append(
            WorkflowStep(
                id=id,
                name=name if name is not None else id,
                agent=agent,
                prompt=prompt,
                depends_on=list(depends_on) if depends_on else [],
                on_failure=on_failure,
                max_retries=max_retries,
            ),
        )
        return self

    def branch(
        self,
        *,
        id: str,
        agent: str,
        prompt: str = "",
        name: str | None = None,
        condition: "Callable[[dict[str, Any]], bool] | None" = None,
        inner_steps: list[WorkflowStep] | None = None,
        depends_on: list[str] | None = None,
        on_failure: str = "abort",
        max_retries: int = 0,
    ) -> "WorkflowBuilder":
        """Add a conditional branch step to the workflow.

        Args:
            id (`str`): Unique step identifier.
            agent (`str`): Agent name.
            prompt (`str`): Prompt (unused when inner steps run).
            name (`str | None`): Human-readable name. Defaults to ``id``.
            condition (`Callable[[dict], bool] | None`):
                Predicate. ``True`` executes inner steps; ``False``
                skips them. ``None`` defaults to always-True.
            inner_steps (`list[WorkflowStep] | None`):
                Steps to run when condition is True.
            depends_on (`list[str] | None`): Step ids that must
                complete first.
            on_failure (`str`): Failure strategy.
            max_retries (`int`): Max retries.

        Returns:
            `WorkflowBuilder`: self, for chaining.
        """
        from ._steps import ConditionalStep

        self._steps.append(
            ConditionalStep(
                id=id,
                name=name if name is not None else id,
                agent=agent,
                prompt=prompt,
                condition=condition
                if condition is not None
                else (lambda ctx: True),
                inner_steps=list(inner_steps) if inner_steps else [],
                depends_on=list(depends_on) if depends_on else [],
                on_failure=on_failure,
                max_retries=max_retries,
            ),
        )
        return self

    def loop(
        self,
        *,
        id: str,
        agent: str,
        prompt: str,
        name: str | None = None,
        condition: "Callable[[dict[str, Any]], bool] | None" = None,
        max_iterations: int = 3,
        depends_on: list[str] | None = None,
        on_failure: str = "abort",
        max_retries: int = 0,
    ) -> "WorkflowBuilder":
        """Add a loop step to the workflow.

        Args:
            id (`str`): Unique step identifier.
            agent (`str`): Agent name.
            prompt (`str`): Prompt sent each iteration.
            name (`str | None`): Human-readable name. Defaults to ``id``.
            condition (`Callable | None`):
                Predicate evaluated before each iteration. ``None``
                defaults to always-False (zero iterations).
            max_iterations (`int`): Hard cap on iteration count.
            depends_on (`list[str] | None`): Step ids that must
                complete first.
            on_failure (`str`): Failure strategy.
            max_retries (`int`): Max retries per iteration.

        Returns:
            `WorkflowBuilder`: self, for chaining.
        """
        from ._steps import LoopStep

        self._steps.append(
            LoopStep(
                id=id,
                name=name if name is not None else id,
                agent=agent,
                prompt=prompt,
                condition=condition
                if condition is not None
                else (lambda ctx: False),
                max_iterations=max_iterations,
                depends_on=list(depends_on) if depends_on else [],
                on_failure=on_failure,
                max_retries=max_retries,
            ),
        )
        return self

    def subworkflow(
        self,
        *,
        id: str,
        workflow: Workflow,
        agent: str = "",
        prompt: str = "",
        name: str | None = None,
        depends_on: list[str] | None = None,
        on_failure: str = "abort",
        max_retries: int = 0,
    ) -> "WorkflowBuilder":
        """Add a sub-workflow step to the workflow.

        Args:
            id (`str`): Unique step identifier.
            workflow (`Workflow`): The nested sub-workflow.
            agent (`str`): Agent name (unused — sub-workflow uses its
                own agents).
            prompt (`str`): Prompt (unused).
            name (`str | None`): Human-readable name. Defaults to ``id``.
            depends_on (`list[str] | None`): Step ids that must
                complete first.
            on_failure (`str`): Failure strategy.
            max_retries (`int`): Max retries.

        Returns:
            `WorkflowBuilder`: self, for chaining.
        """
        from ._steps import SubWorkflowStep

        self._steps.append(
            SubWorkflowStep(
                id=id,
                name=name if name is not None else id,
                agent=agent,
                prompt=prompt,
                sub_workflow=workflow,
                depends_on=list(depends_on) if depends_on else [],
                on_failure=on_failure,
                max_retries=max_retries,
            ),
        )
        return self

    def sleep(
        self,
        *,
        id: str,
        duration_seconds: int,
        agent: str = "",
        prompt: str = "",
        name: str | None = None,
        depends_on: list[str] | None = None,
        on_failure: str = "abort",
        max_retries: int = 0,
    ) -> "WorkflowBuilder":
        """Add a timer (sleep) step to the workflow.

        Args:
            id (`str`): Unique step identifier.
            duration_seconds (`int`): Sleep duration in seconds.
            agent (`str`): Agent name (unused).
            prompt (`str`): Prompt (unused).
            name (`str | None`): Human-readable name. Defaults to ``id``.
            depends_on (`list[str] | None`): Step ids that must
                complete first.
            on_failure (`str`): Failure strategy.
            max_retries (`int`): Max retries.

        Returns:
            `WorkflowBuilder`: self, for chaining.
        """
        from ._steps import TimerStep

        self._steps.append(
            TimerStep(
                id=id,
                name=name if name is not None else id,
                agent=agent,
                prompt=prompt,
                duration_seconds=duration_seconds,
                depends_on=list(depends_on) if depends_on else [],
                on_failure=on_failure,
                max_retries=max_retries,
            ),
        )
        return self

    def approval(
        self,
        *,
        id: str,
        approver: str,
        timeout_seconds: int = 3600,
        on_timeout: str = "reject",
        agent: str = "",
        prompt: str = "",
        name: str | None = None,
        depends_on: list[str] | None = None,
        on_failure: str = "abort",
        max_retries: int = 0,
    ) -> "WorkflowBuilder":
        """Add an approval (HITL) step to the workflow.

        Args:
            id (`str`): Unique step identifier.
            approver (`str`): Approver identifier (user_id / email).
            timeout_seconds (`int`): Approval timeout in seconds.
            on_timeout (`str`): Timeout policy — ``"reject"``
                (default), ``"approve"``, or ``"abort"``.
            agent (`str`): Agent name (unused).
            prompt (`str`): Prompt (unused).
            name (`str | None`): Human-readable name. Defaults to ``id``.
            depends_on (`list[str] | None`): Step ids that must
                complete first.
            on_failure (`str`): Failure strategy.
            max_retries (`int`): Max retries.

        Returns:
            `WorkflowBuilder`: self, for chaining.
        """
        from ._approval import ApprovalStep

        self._steps.append(
            ApprovalStep(
                id=id,
                name=name if name is not None else id,
                agent=agent,
                prompt=prompt,
                approver=approver,
                timeout_seconds=timeout_seconds,
                on_timeout=on_timeout,
                depends_on=list(depends_on) if depends_on else [],
                on_failure=on_failure,
                max_retries=max_retries,
            ),
        )
        return self

    def build(self) -> Workflow:
        """Build the :class:`Workflow` instance.

        If no id was set, a unique id is auto-generated.  If no name
        was set, it defaults to the id.

        Returns:
            `Workflow`: The constructed workflow.
        """
        wf_id = self._id or f"wf-{uuid.uuid4().hex[:8]}"
        wf_name = self._name if self._name is not None else wf_id
        return Workflow(id=wf_id, name=wf_name, steps=list(self._steps))


# ── FunctionExecutor ──────────────────────────────────────────────


# Type for the sync callable wrapped by FunctionExecutor.
SyncStepFn = Callable[[WorkflowStep, dict[str, Any]], str]


class FunctionExecutor:
    """Wraps a sync callable into an async :class:`StepExecutor`.

    The wrapped function receives ``(step, context)`` and returns a
    ``str``.  Exceptions are caught and converted to ``None`` (the
    :class:`Orchestrator` treats ``None`` as a step failure, honoring
    the step's ``on_failure`` strategy).

    Useful for tests, evals, and simple workflows that don't need to
    drive real agent sessions.

    Args:
        fn (`SyncStepFn`):
            Sync callable ``(step, context) -> str``.
    """

    def __init__(self, fn: SyncStepFn) -> None:
        """Initialize the executor with a sync callable."""
        self._fn = fn

    async def __call__(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
    ) -> str | None:
        """Execute the wrapped function asynchronously.

        Args:
            step (`WorkflowStep`): The step to execute.
            context (`dict[str, Any]`): The accumulated context.

        Returns:
            `str | None`: The function's return value, or ``None``
            if the function raised.
        """
        try:
            return self._fn(step, context)
        except Exception:  # noqa: BLE001
            # Mirror Orchestrator._execute_step semantics: a raised
            # exception yields None, which Orchestrator treats as a
            # step failure (subject to on_failure strategy).
            return None


# ── Convenience functions ─────────────────────────────────────────


async def run_workflow(
    workflow: Workflow,
    executor: StepExecutor,
    *,
    store: CheckpointStore | None = None,
    ttl_seconds: int | None = None,
    tracer: Any = None,
    metrics: Any = None,
    audit: Any = None,
) -> WorkflowResult:
    """Run a workflow with optional checkpoint persistence and tracing.

    When ``store`` is ``None``, runs the workflow in-memory using a
    plain :class:`Orchestrator` (no durability).  When a store is
    provided, uses :class:`CheckpointedOrchestrator` so each layer's
    state is persisted — enabling later :func:`resume_workflow` calls.

    When ``tracer`` is provided (a :class:`WorkflowTracer` or any
    object with ``start_workflow_span`` / ``start_step_span``), the
    workflow execution is wrapped in OTel-style spans for
    distributed tracing.

    When ``metrics`` is provided (a :class:`WorkflowMetrics`), step
    counts / durations / checkpoint saves are recorded for
    Prometheus export.

    Args:
        workflow (`Workflow`): The workflow to execute.
        executor (`StepExecutor`):
            Async callable ``(step, context) -> str``.  A
            :class:`FunctionExecutor` works here.
        store (`CheckpointStore | None`):
            Optional checkpoint store.  When ``None``, no
            checkpoints are saved.
        ttl_seconds (`int | None`):
            Optional checkpoint TTL.  Only used when ``store`` is
            provided.
        tracer (`Any`):
            Optional workflow tracer.  When ``None``, no tracing.
            Use :class:`WorkflowTracer` for OTel export or
            :class:`NoOpWorkflowTracer` for explicit no-op.
        metrics (`Any`):
            Optional :class:`WorkflowMetrics` collector.  When
            ``None``, no metrics are recorded.

    Returns:
        `WorkflowResult`: The execution result.
    """
    if store is None:
        orch = Orchestrator(executor=executor)
    else:
        orch = CheckpointedOrchestrator(
            executor=executor,
            store=store,
            checkpoint_ttl_seconds=ttl_seconds,
        )
    if tracer is not None:
        orch._tracer = tracer
    if metrics is not None:
        orch._metrics = metrics
    if audit is not None:
        orch._audit = audit
    return await orch.run(workflow)


async def resume_workflow(
    workflow: Workflow,
    *,
    store: CheckpointStore,
    executor: StepExecutor | None = None,
) -> WorkflowResult | None:
    """Resume a workflow from its latest checkpoint.

    Loads the latest checkpoint for ``workflow.id`` and continues
    execution from the next pending layer.  Steps with ``COMPLETED``
    status in the checkpoint are skipped — their outputs are reused.

    When the latest checkpoint is already ``COMPLETED`` or ``FAILED``,
    the result is returned without re-executing any steps (no
    executor needed).

    Args:
        workflow (`Workflow`): The workflow definition.
        store (`CheckpointStore`): The checkpoint store.
        executor (`StepExecutor | None`):
            Async callable for executing pending steps.  Required
            when the workflow is not yet complete; ignored otherwise.

    Returns:
        `WorkflowResult | None`: The execution result, or ``None``
        if no checkpoint exists for the workflow.
    """
    # Peek at the latest checkpoint to decide whether an executor is
    # needed.  When the workflow is already finished, no executor is
    # required.
    latest = await store.latest_for_workflow(workflow.id)
    if latest is None:
        return None
    if latest.status in ("COMPLETED", "FAILED") and executor is None:
        # No executor provided but the workflow is already done —
        # rebuild the result from the checkpoint directly.
        from ._orchestrator import _result_from_checkpoint
        from .._orchestrator import WorkflowStatus

        status = (
            WorkflowStatus.FAILED
            if latest.status == "FAILED"
            else WorkflowStatus.COMPLETED
        )
        return _result_from_checkpoint(latest, status=status)

    if executor is None:
        raise ValueError(
            "An executor is required to resume a workflow that is "
            "not yet COMPLETED or FAILED.",
        )

    orch = CheckpointedOrchestrator(executor=executor, store=store)
    return await orch.resume(workflow)


def load_workflow_from_file(path: str | Path) -> Workflow:
    """Load a workflow from a YAML file on disk.

    Args:
        path (`str | Path`): Path to the YAML file.

    Returns:
        `Workflow`: The parsed workflow.

    Raises:
        `FileNotFoundError`: If the file does not exist.
        `ValueError`: If the YAML is missing required fields.
        `yaml.YAMLError`: If the YAML is syntactically invalid.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")
    text = path.read_text(encoding="utf-8")
    return parse_workflow_yaml(text)
