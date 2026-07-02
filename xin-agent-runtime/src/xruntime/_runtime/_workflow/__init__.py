# -*- coding: utf-8 -*-
"""Workflow Checkpoint + SDK module — durable execution on top of the
in-memory Orchestrator plus a high-level public API.

Public surface:

* :class:`Checkpoint` — pydantic data model with TTL + chain.
* :class:`CheckpointStore` — async ABC for storage backends.
* :class:`InMemoryCheckpointStore` — dict-backed store for tests / dev.
* :class:`CheckpointedOrchestrator` — wraps :class:`Orchestrator` with
  per-layer checkpoint persistence and :meth:`resume`.
* :class:`WorkflowConfig` — pydantic config wired into
  :class:`XRuntimeConfig.workflow`.
* :class:`CheckpointStatus` — status string constants.
* :class:`WorkflowBuilder` — fluent builder for :class:`Workflow`.
* :class:`FunctionExecutor` — wraps a sync callable into an async
  :class:`StepExecutor`.
* :func:`run_workflow` — one-shot convenience runner.
* :func:`resume_workflow` — resume from latest checkpoint.
* :func:`load_workflow_from_file` — load a YAML workflow from disk.
"""
from ._checkpoint import (
    Checkpoint,
    CheckpointStatus,
    CheckpointStore,
    InMemoryCheckpointStore,
)
from ._config import WorkflowConfig
from ._orchestrator import CheckpointedOrchestrator
from ._sdk import (
    FunctionExecutor,
    WorkflowBuilder,
    load_workflow_from_file,
    resume_workflow,
    run_workflow,
)
from ._steps import (
    ConditionalStep,
    LoopStep,
    SubWorkflowStep,
    TimerStep,
)
from ._approval import (
    ApprovalRequest,
    ApprovalStep,
    ApprovalStore,
    InMemoryApprovalStore,
)
from ._tracer import (
    InMemoryWorkflowTracer,
    NoOpWorkflowTracer,
    WorkflowTracer,
)
from ._metrics import WorkflowMetrics
from ._audit import WorkflowAuditLogger

__all__ = [
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
    "ConditionalStep",
    "LoopStep",
    "SubWorkflowStep",
    "TimerStep",
    "ApprovalRequest",
    "ApprovalStep",
    "ApprovalStore",
    "InMemoryApprovalStore",
    "WorkflowTracer",
    "InMemoryWorkflowTracer",
    "NoOpWorkflowTracer",
    "WorkflowMetrics",
    "WorkflowAuditLogger",
]
