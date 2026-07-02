# -*- coding: utf-8 -*-
"""CheckpointedOrchestrator — wraps the in-memory Orchestrator with
durable per-layer checkpoints.

The existing :class:`Orchestrator` runs a DAG workflow entirely
in-memory: if the process dies mid-run, all progress is lost.  This
wrapper adds durability by persisting a :class:`Checkpoint` after
each topological layer completes (and a final COMPLETED / FAILED
checkpoint at the end).

Resume is the key new capability: :meth:`resume` loads the latest
checkpoint for a workflow, rebuilds the :class:`WorkflowResult` from
the checkpoint's ``step_results`` / ``step_status`` / ``context``,
and continues execution from the next pending layer.  Steps that
already have a ``COMPLETED`` status are skipped — their outputs are
reused from the checkpoint.

Integration pattern (no AS core changes)::

    from xruntime._runtime._workflow import (
        CheckpointedOrchestrator, InMemoryCheckpointStore,
    )
    from xruntime._runtime._orchestrator import Workflow

    orch = CheckpointedOrchestrator(
        executor=my_executor,
        store=InMemoryCheckpointStore(),
    )
    result = await orch.run(workflow)          # persists checkpoints
    result2 = await orch.resume(workflow)      # loads + continues
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .._orchestrator import (
    Orchestrator,
    StepExecutor,
    StepStatus,
    Workflow,
    WorkflowResult,
    WorkflowStatus,
    WorkflowStep,
)
from ._checkpoint import Checkpoint, CheckpointStatus, CheckpointStore


class CheckpointedOrchestrator(Orchestrator):
    """Orchestrator with durable per-layer checkpoints.

    Args:
        executor (`StepExecutor`):
            Async callable ``(step, context) -> result`` — same
            contract as :class:`Orchestrator`.
        store (`CheckpointStore`):
            The checkpoint store.  Use :class:`InMemoryCheckpointStore`
            for tests / dev.
        checkpoint_ttl_seconds (`int | None`):
            Optional TTL for saved checkpoints.  ``None`` uses the
            store default (no expiry).
    """

    def __init__(
        self,
        executor: StepExecutor,
        store: CheckpointStore,
        *,
        checkpoint_ttl_seconds: int | None = None,
    ) -> None:
        super().__init__(executor=executor)
        self._store = store
        self._checkpoint_ttl = checkpoint_ttl_seconds

    def _record_checkpoint_save(self, workflow_id: str) -> None:
        """Record a checkpoint save in the metrics collector (if set)."""
        metrics = getattr(self, "_metrics", None)
        if metrics is not None:
            metrics.record_checkpoint_save(workflow_id)

    @property
    def store(self) -> CheckpointStore:
        """Return the checkpoint store (exposed for tests / debug)."""
        return self._store

    async def run(self, workflow: Workflow) -> WorkflowResult:
        """Execute a workflow with per-layer checkpoint persistence.

        Mirrors :meth:`Orchestrator.run` but saves a
        :class:`Checkpoint` after each topological layer completes.
        On workflow completion (or abort), a final COMPLETED (or
        FAILED) checkpoint is saved.

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
        parent_cp_id: str | None = None
        layers = self._group_into_layers(workflow, order)

        for layer in layers:
            runnable = self._compute_runnable(layer, step_map, result)
            if not runnable:
                continue

            tasks = [
                self._execute_step(step, dict(context), result)
                for step in runnable
            ]
            outputs = await _gather_strict(tasks)

            abort = False
            for step, output in zip(runnable, outputs):
                if output is None:
                    result.step_status[step.id] = StepStatus.FAILED
                    if step.on_failure == "abort":
                        result.status = WorkflowStatus.FAILED
                        abort = True
                    else:
                        context[step.id] = ""
                else:
                    result.step_status[step.id] = StepStatus.COMPLETED
                    result.step_results[step.id] = output
                    context[step.id] = output

            # Persist a checkpoint after the layer settles, regardless
            # of whether this layer aborted — the checkpoint captures
            # the durable state up to this point so resume can continue
            # (or report failure) without re-running completed steps.
            last_step_id = runnable[-1].id if runnable else ""
            last_step_name = (
                step_map[last_step_id].name if last_step_id else ""
            )
            cp = Checkpoint(
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                step_id=last_step_id,
                step_name=last_step_name,
                step_results=dict(result.step_results),
                step_status={
                    k: v.value if hasattr(v, "value") else str(v)
                    for k, v in result.step_status.items()
                },
                context=_sanitize_context(context),
                status=CheckpointStatus.ACTIVE,
                parent_checkpoint_id=parent_cp_id,
                expires_at=self._compute_expiry(),
            )
            await self._store.save(cp)
            self._record_checkpoint_save(workflow.id)
            parent_cp_id = cp.checkpoint_id

            if abort:
                # Mark every not-yet-run step as skipped.
                for remaining_id in order:
                    if (
                        remaining_id not in result.step_status
                        or result.step_status[remaining_id]
                        == StepStatus.PENDING
                    ):
                        result.step_status[remaining_id] = StepStatus.SKIPPED
                # Update the latest checkpoint to FAILED.
                cp.status = CheckpointStatus.FAILED
                await self._store.save(cp)
                self._record_checkpoint_save(workflow.id)
                return result

        if result.status != WorkflowStatus.FAILED:
            result.status = WorkflowStatus.COMPLETED

        # Save a final COMPLETED checkpoint (or update the last one).
        final_cp = Checkpoint(
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            step_id=last_step_id if layers else "",
            step_name=(
                step_map[last_step_id].name if last_step_id and layers else ""
            ),
            step_results=dict(result.step_results),
            step_status={
                k: v.value if hasattr(v, "value") else str(v)
                for k, v in result.step_status.items()
            },
            context=_sanitize_context(context),
            status=CheckpointStatus.COMPLETED,
            parent_checkpoint_id=parent_cp_id,
            expires_at=self._compute_expiry(),
        )
        await self._store.save(final_cp)
        self._record_checkpoint_save(workflow.id)

        return result

    async def resume(self, workflow: Workflow) -> WorkflowResult | None:
        """Resume a workflow from its latest checkpoint.

        Loads the latest checkpoint for ``workflow.id`` and continues
        execution from the next pending layer.  Steps with
        ``COMPLETED`` status in the checkpoint are skipped — their
        outputs are reused.

        Args:
            workflow (`Workflow`):
                The workflow definition (needed to compute the
                topological order and step map).

        Returns:
            `WorkflowResult | None`: The execution result, or
            ``None`` if no checkpoint exists for the workflow.
        """
        latest = await self._store.latest_for_workflow(workflow.id)
        if latest is None:
            return None

        # If the workflow already finished, return the result without
        # re-executing anything.
        if latest.status == CheckpointStatus.COMPLETED:
            return _result_from_checkpoint(latest)
        if latest.status == CheckpointStatus.FAILED:
            return _result_from_checkpoint(latest, WorkflowStatus.FAILED)

        # ACTIVE checkpoint — rebuild state and continue.
        result = WorkflowResult(status=WorkflowStatus.RUNNING)
        order = workflow.topological_order()
        step_map = {s.id: s for s in workflow.steps}

        # Restore step_status and step_results from the checkpoint.
        for step_id in order:
            cp_status = latest.step_status.get(step_id, StepStatus.PENDING)
            # Convert string back to StepStatus enum
            try:
                result.step_status[step_id] = StepStatus(cp_status)
            except ValueError:
                result.step_status[step_id] = StepStatus.PENDING
            if result.step_status[step_id] == StepStatus.COMPLETED:
                result.step_results[step_id] = latest.step_results.get(
                    step_id,
                    "",
                )

        context: dict[str, Any] = dict(latest.context)
        parent_cp_id = latest.checkpoint_id
        layers = self._group_into_layers(workflow, order)

        # Skip layers that are already fully completed.
        # A layer is "done" if all its steps are COMPLETED.
        start_layer_idx = 0
        for i, layer in enumerate(layers):
            if all(
                result.step_status.get(sid) == StepStatus.COMPLETED
                for sid in layer
            ):
                start_layer_idx = i + 1
            else:
                break

        for layer in layers[start_layer_idx:]:
            runnable = self._compute_runnable(layer, step_map, result)
            if not runnable:
                continue

            tasks = [
                self._execute_step(step, dict(context), result)
                for step in runnable
            ]
            outputs = await _gather_strict(tasks)

            abort = False
            for step, output in zip(runnable, outputs):
                if output is None:
                    result.step_status[step.id] = StepStatus.FAILED
                    if step.on_failure == "abort":
                        result.status = WorkflowStatus.FAILED
                        abort = True
                    else:
                        context[step.id] = ""
                else:
                    result.step_status[step.id] = StepStatus.COMPLETED
                    result.step_results[step.id] = output
                    context[step.id] = output

            last_step_id = runnable[-1].id if runnable else ""
            last_step_name = (
                step_map[last_step_id].name if last_step_id else ""
            )
            cp = Checkpoint(
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                step_id=last_step_id,
                step_name=last_step_name,
                step_results=dict(result.step_results),
                step_status={
                    k: v.value if hasattr(v, "value") else str(v)
                    for k, v in result.step_status.items()
                },
                context=_sanitize_context(context),
                status=CheckpointStatus.ACTIVE,
                parent_checkpoint_id=parent_cp_id,
                expires_at=self._compute_expiry(),
            )
            await self._store.save(cp)
            self._record_checkpoint_save(workflow.id)
            parent_cp_id = cp.checkpoint_id

            if abort:
                for remaining_id in order:
                    if (
                        remaining_id not in result.step_status
                        or result.step_status[remaining_id]
                        == StepStatus.PENDING
                    ):
                        result.step_status[remaining_id] = StepStatus.SKIPPED
                cp.status = CheckpointStatus.FAILED
                await self._store.save(cp)
                self._record_checkpoint_save(workflow.id)
                return result

        if result.status != WorkflowStatus.FAILED:
            result.status = WorkflowStatus.COMPLETED

        final_cp = Checkpoint(
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            step_id=last_step_id if layers else "",
            step_name=(
                step_map[last_step_id].name if last_step_id and layers else ""
            ),
            step_results=dict(result.step_results),
            step_status={
                k: v.value if hasattr(v, "value") else str(v)
                for k, v in result.step_status.items()
            },
            context=_sanitize_context(context),
            status=CheckpointStatus.COMPLETED,
            parent_checkpoint_id=parent_cp_id,
            expires_at=self._compute_expiry(),
        )
        await self._store.save(final_cp)
        self._record_checkpoint_save(workflow.id)

        return result

    def _compute_runnable(
        self,
        layer: list[str],
        step_map: dict[str, WorkflowStep],
        result: WorkflowResult,
    ) -> list[WorkflowStep]:
        """Compute which steps in a layer can run, mirroring the
        base Orchestrator's skip logic."""
        runnable: list[WorkflowStep] = []
        for step_id in layer:
            step = step_map[step_id]
            # Skip steps that already completed (resume case)
            if result.step_status.get(step_id) == StepStatus.COMPLETED:
                continue
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
        return runnable

    def _compute_expiry(self) -> float | None:
        """Return the expiry timestamp for a new checkpoint."""
        if self._checkpoint_ttl is None or self._checkpoint_ttl <= 0:
            return None
        import time as _time

        return _time.time() + self._checkpoint_ttl


async def _gather_strict(
    tasks: list[Awaitable[str | None]],
) -> list[str | None]:
    """Gather awaitables, preserving order and returning None for
    exceptions (mirrors the base Orchestrator's
    ``_execute_step`` semantics)."""
    import asyncio

    # Use return_exceptions=True so one step's failure doesn't cancel
    # siblings — the base Orchestrator's _execute_step catches
    # exceptions and returns None, but we double-guard here.
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[str | None] = []
    for item in raw:
        if isinstance(item, Exception):
            out.append(None)
        else:
            out.append(item)
    return out


def _sanitize_context(context: dict[str, Any]) -> dict[str, Any]:
    """Ensure the context dict is JSON-serializable.

    The StepExecutor contract returns ``str``, so values should
    already be strings — but this guard ensures non-string values
    are stringified rather than crashing the checkpoint save.
    """
    return {
        k: (
            v
            if isinstance(v, (str, int, float, bool, list, dict)) or v is None
            else str(v)
        )
        for k, v in context.items()
    }


def _result_from_checkpoint(
    cp: Checkpoint,
    status: WorkflowStatus = WorkflowStatus.COMPLETED,
) -> WorkflowResult:
    """Rebuild a WorkflowResult from a completed/failed checkpoint."""
    result = WorkflowResult(status=status)
    result.step_results = dict(cp.step_results)
    result.step_status = {
        k: StepStatus(v)
        if v in StepStatus._value2member_map_
        else StepStatus.PENDING
        for k, v in cp.step_status.items()
    }
    return result
