# -*- coding: utf-8 -*-
"""Extended workflow step types for P3-A control flow.

This module adds runtime-branching step types on top of the base
:class:`WorkflowStep`:

* :class:`ConditionalStep` — wraps a list of inner steps and a
  ``condition`` predicate.  When the predicate evaluates to ``True``
  on the workflow context, the inner steps are executed; when
  ``False``, they are all marked ``SKIPPED`` and the branch produces
  an empty output.  Multiple branches can be declared — the
  orchestrator evaluates each branch's condition independently
  (no mutual exclusion is enforced at the step level; callers get
  mutual exclusion by making the conditions mutually exclusive).
* :class:`LoopStep` — (P3-A Task 2) executes a step repeatedly while
  a condition holds, up to ``max_iterations``.
* :class:`SubWorkflowStep` — (P3-A Task 3) nests a full sub-workflow
  as a single step.
* :class:`TimerStep` — (P3-A Task 4) durable sleep that checkpoints
  ``SLEEPING`` + ``wake_at`` and resumes when the timer elapses.

Design notes:

* All new step types subclass :class:`WorkflowStep` so they slot
  into the existing DAG without changes to the base orchestrator's
  topological sort.  The orchestrator checks ``isinstance(step,
  ConditionalStep)`` (etc.) to dispatch to the right execution path.
* ``condition`` predicates are sync callables ``dict[str, Any] ->
  bool``.  Exceptions are swallowed (return ``False``) so a buggy
  predicate fails closed — the branch is skipped rather than
  crashing the workflow.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .._orchestrator import Workflow, WorkflowStep

if TYPE_CHECKING:
    pass


logger = logging.getLogger("xruntime.workflow.steps")


# Type alias for condition predicates.
ConditionFn = Callable[[dict[str, Any]], bool]


# ── ConditionalStep ─────────────────────────────────────────────


@dataclass
class ConditionalStep(WorkflowStep):
    """A step that conditionally executes a list of inner steps.

    When ``condition(context)`` returns ``True``, the inner steps are
    executed in sequence (preserving their own ``depends_on``
    ordering).  The branch's output is the output of the last inner
    step.  When ``condition`` returns ``False``, all inner steps are
    marked ``SKIPPED`` and the branch's output is an empty string.

    Args:
        id (`str`): Unique step identifier.
        name (`str`): Human-readable step name.
        agent (`str`): Agent name (used when the branch itself is
            executed as a step — inner steps use their own agents).
        prompt (`str`): Prompt (unused when inner steps run; kept
            for :class:`WorkflowStep` compatibility).
        condition (`ConditionFn`):
            Predicate ``dict[str, Any] -> bool``.  ``True`` executes
            the inner steps; ``False`` skips them.  Exceptions are
            swallowed (fail-closed: skip).
        inner_steps (`list[WorkflowStep]`):
            The steps to run when the condition is ``True``.  Their
            ``depends_on`` fields are honoured within the branch.
        depends_on (`list[str]`):
            Step ids that must complete before this branch is
            evaluated.
        on_failure (`str`):
            Failure strategy for the branch (applies to inner steps).
        max_retries (`int`):
            Max retries (applies to inner steps).
    """

    condition: ConditionFn = field(default=lambda ctx: True)
    inner_steps: list[WorkflowStep] = field(default_factory=list)

    def eval_condition(self, context: dict[str, Any]) -> bool:
        """Evaluate the condition predicate, fail-closed.

        Args:
            context (`dict[str, Any]`): The workflow context.

        Returns:
            `bool`: ``True`` if the branch should execute.  Any
            exception from the predicate is logged and returns
            ``False`` (fail-closed).
        """
        try:
            return bool(self.condition(context))
        except Exception:  # noqa: BLE001
            logger.exception(
                "ConditionalStep '%s' condition raised; failing "
                "closed (skip branch)",
                self.id,
            )
            return False


# ── LoopStep (P3-A Task 2 — placeholder) ────────────────────────


@dataclass
class LoopStep(WorkflowStep):
    """A step that repeats while a condition holds.

    Each iteration executes the step's ``agent`` / ``prompt``.  The
    loop exits when ``condition(context)`` returns ``False`` or when
    ``max_iterations`` is reached.

    Args:
        id (`str`): Unique step identifier.
        name (`str`): Human-readable step name.
        agent (`str`): Agent name.
        prompt (`str`): Prompt sent to the agent each iteration.
        condition (`ConditionFn`):
            Predicate evaluated *before* each iteration.  ``True``
            continues; ``False`` exits.
        max_iterations (`int`):
            Hard cap on iteration count.  ``0`` means no iterations
            (the loop body never runs).
        depends_on (`list[str]`):
            Step ids that must complete first.
        on_failure (`str`): Failure strategy.
        max_retries (`int`): Max retries per iteration.
    """

    condition: ConditionFn = field(default=lambda ctx: False)
    max_iterations: int = 1


# ── SubWorkflowStep (P3-A Task 3 — placeholder) ─────────────────


@dataclass
class SubWorkflowStep(WorkflowStep):
    """A step that runs a nested sub-workflow.

    The sub-workflow is executed as a single unit.  Its result (the
    output of the sub-workflow's last step) becomes this step's
    output.  Checkpoints for the sub-workflow are namespaced as
    ``{parent_workflow_id}/{step_id}`` so they don't collide with
    the parent's checkpoints.

    Args:
        id (`str`): Unique step identifier.
        name (`str`): Human-readable step name.
        agent (`str`): Agent name (unused — sub-workflow uses its
            own agents; kept for :class:`WorkflowStep` compatibility).
        prompt (`str`): Prompt (unused; kept for compatibility).
        sub_workflow (`Workflow`):
            The nested workflow to execute.
        depends_on (`list[str]`):
            Step ids that must complete first.
        on_failure (`str`): Failure strategy.
        max_retries (`int`): Max retries.
    """

    sub_workflow: Workflow = field(
        default_factory=lambda: Workflow(id="", name="", steps=[]),
    )


# ── TimerStep (P3-A Task 4 — placeholder) ───────────────────────


@dataclass
class TimerStep(WorkflowStep):
    """A durable sleep step.

    When the orchestrator reaches a :class:`TimerStep`, it saves a
    checkpoint with ``status=SLEEPING`` and ``wake_at = now +
    duration_seconds``, then pauses the workflow.  Resume checks
    ``wake_at``: if the current time is before it, the workflow
    stays paused; otherwise execution continues with the next step.

    Args:
        id (`str`): Unique step identifier.
        name (`str`): Human-readable step name.
        agent (`str`): Agent name (unused; kept for compatibility).
        prompt (`str`): Prompt (unused; kept for compatibility).
        duration_seconds (`int`):
            How long to sleep before resuming.
        depends_on (`list[str]`):
            Step ids that must complete first.
        on_failure (`str`): Failure strategy.
        max_retries (`int`): Max retries.
    """

    duration_seconds: int = 0


__all__ = [
    "ConditionalStep",
    "LoopStep",
    "SubWorkflowStep",
    "TimerStep",
]
