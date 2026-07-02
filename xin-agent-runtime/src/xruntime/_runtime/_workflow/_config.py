# -*- coding: utf-8 -*-
"""Workflow configuration for XRuntimeConfig.

Wired into :class:`XRuntimeConfig.workflow` so the checkpoint module
can be enabled / tuned via YAML or ``XRUNTIME_WORKFLOW_*`` env vars.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class WorkflowConfig(BaseModel):
    """Workflow + checkpoint configuration.

    Args:
        enabled (`bool`):
            Whether the :class:`CheckpointedOrchestrator` is active.
            When ``False``, the runtime uses the plain in-memory
            :class:`Orchestrator` (no persistence).
        default_checkpoint_ttl_seconds (`int`):
            Default TTL for checkpoints.  ``0`` means no expiry.
        store_backend (`str`):
            Checkpoint store backend — ``"memory"`` (default, for
            tests / dev) or ``"redis"`` (lazy-imported in
            production).
        redis_prefix (`str`):
            Redis key prefix for checkpoint storage (when
            ``store_backend == "redis"``).
    """

    enabled: bool = False
    default_checkpoint_ttl_seconds: int = 86400
    store_backend: str = "memory"
    redis_prefix: str = "xruntime:workflow:cp:"
