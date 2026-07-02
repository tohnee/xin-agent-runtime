# -*- coding: utf-8 -*-
"""Checkpoint data model + store ABC + in-memory implementation.

A :class:`Checkpoint` captures the durable state of a workflow at a
single point in time — typically after a topological layer of steps
completes.  The checkpoint is self-contained: it carries the
accumulated ``step_results`` / ``step_status`` / ``context`` so a
:class:`CheckpointedOrchestrator` can resume execution from any
checkpoint without re-running completed steps.

Design notes:

* :class:`Checkpoint` is a pydantic ``BaseModel`` (not a dataclass) so
  it serializes cleanly to JSON for Redis storage.  ``to_dict`` /
  ``from_dict`` provide the dict round-trip used by stores.
* :class:`CheckpointStore` is an async ABC — production stores
  (Redis, Postgres) live in sibling modules and lazy-import their
  backend.  :class:`InMemoryCheckpointStore` is the test / dev
  default and is also used by the in-process eval target.
* TTL is optional (``expires_at``).  Expired checkpoints are treated
  as missing by ``load`` / ``latest_for_workflow`` — they are not
  auto-evicted (cheap to add later via a background sweep).
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


class CheckpointStatus:
    """Checkpoint status constants (string literals, not an Enum —
    keeps serialization trivial and matches the existing
    ``WorkflowStatus`` style)."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Checkpoint(BaseModel):
    """A durable snapshot of a workflow's progress.

    Args:
        checkpoint_id (`str`):
            Unique identifier (auto-generated).
        workflow_id (`str`):
            The workflow this checkpoint belongs to.
        workflow_name (`str`):
            Human-readable workflow name (for display / debug).
        step_id (`str`):
            The step that just completed (``""`` for the initial
            checkpoint before any step runs).
        step_name (`str`):
            Human-readable step name.
        step_results (`dict[str, str]`):
            Outputs of completed steps, keyed by step id.
        step_status (`dict[str, str]`):
            Status of each step (``COMPLETED`` / ``FAILED`` /
            ``SKIPPED``).
        context (`dict[str, Any]`):
            The accumulated context dict passed between steps.
            JSON-serializable.
        status (`str`):
            Checkpoint status — ``ACTIVE`` (in-progress),
            ``COMPLETED`` (workflow finished), ``FAILED`` (abort).
        parent_checkpoint_id (`str | None`):
            Previous checkpoint in the chain (``None`` for the
            initial checkpoint).  Enables time-travel debugging.
        created_at (`float`):
            Unix timestamp (seconds) when the checkpoint was saved.
        expires_at (`float | None`):
            Optional TTL — when set, the checkpoint is treated as
            missing after this timestamp.
    """

    checkpoint_id: str = Field(
        default_factory=lambda: f"cp-{uuid.uuid4().hex[:12]}"
    )
    workflow_id: str
    workflow_name: str = ""
    step_id: str = ""
    step_name: str = ""
    step_results: dict[str, str] = Field(default_factory=dict)
    step_status: dict[str, str] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    status: str = CheckpointStatus.ACTIVE
    parent_checkpoint_id: str | None = None
    created_at: float = Field(default_factory=time.time)
    expires_at: float | None = None

    def is_expired(self, now: float | None = None) -> bool:
        """Return ``True`` if the checkpoint's TTL has elapsed.

        Args:
            now (`float | None`):
                Reference timestamp.  ``None`` uses ``time.time()``.

        Returns:
            `bool`: ``True`` if expired, ``False`` if no TTL or
            still within TTL.
        """
        if self.expires_at is None:
            return False
        if now is None:
            now = time.time()
        return now >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for store backends.

        Returns:
            `dict[str, Any]`: JSON-serializable dict.
        """
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Checkpoint":
        """Deserialize from a plain dict.

        Args:
            data (`dict[str, Any]`):
                Dict produced by :meth:`to_dict`.

        Returns:
            `Checkpoint`: The reconstructed checkpoint.
        """
        return cls.model_validate(data)


class CheckpointStore:
    """Abstract base class for checkpoint storage backends.

    All methods are async — backends may be network-bound (Redis,
    Postgres).  The in-memory implementation is in
    :class:`InMemoryCheckpointStore`.
    """

    async def save(self, checkpoint: Checkpoint) -> str:
        """Persist a checkpoint.  Overwrites if the id already exists.

        Args:
            checkpoint (`Checkpoint`):
                The checkpoint to save.

        Returns:
            `str`: The checkpoint id.
        """
        raise NotImplementedError

    async def load(self, checkpoint_id: str) -> Checkpoint | None:
        """Load a checkpoint by id.

        Args:
            checkpoint_id (`str`):
                The checkpoint id.

        Returns:
            `Checkpoint | None`: The checkpoint, or ``None`` if not
            found or expired.
        """
        raise NotImplementedError

    async def latest_for_workflow(
        self,
        workflow_id: str,
    ) -> Checkpoint | None:
        """Return the most recent non-expired checkpoint for a workflow.

        Args:
            workflow_id (`str`):
                The workflow id.

        Returns:
            `Checkpoint | None`: The latest checkpoint, or ``None``.
        """
        raise NotImplementedError

    async def list_by_workflow(
        self,
        workflow_id: str,
    ) -> list[Checkpoint]:
        """List all checkpoints for a workflow, oldest first.

        Args:
            workflow_id (`str`):
                The workflow id.

        Returns:
            `list[Checkpoint]`: All non-expired checkpoints, ordered
            by ``created_at`` ascending.
        """
        raise NotImplementedError

    async def delete(self, checkpoint_id: str) -> bool:
        """Delete a single checkpoint.

        Args:
            checkpoint_id (`str`):
                The checkpoint id.

        Returns:
            `bool`: ``True`` if deleted, ``False`` if not found.
        """
        raise NotImplementedError

    async def delete_by_workflow(self, workflow_id: str) -> int:
        """Delete all checkpoints for a workflow.

        Args:
            workflow_id (`str`):
                The workflow id.

        Returns:
            `int`: Number of checkpoints deleted.
        """
        raise NotImplementedError


class InMemoryCheckpointStore(CheckpointStore):
    """Dict-backed in-memory checkpoint store.

    Suitable for tests, dev runs, and the in-process eval target.
    Not suitable for production (no cross-process sharing, no
    persistence across restarts).
    """

    def __init__(self) -> None:
        self._store: dict[str, Checkpoint] = {}

    async def save(self, checkpoint: Checkpoint) -> str:
        self._store[checkpoint.checkpoint_id] = checkpoint
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: str) -> Checkpoint | None:
        cp = self._store.get(checkpoint_id)
        if cp is None:
            return None
        if cp.is_expired():
            return None
        return cp

    async def latest_for_workflow(
        self,
        workflow_id: str,
    ) -> Checkpoint | None:
        candidates = [
            cp
            for cp in self._store.values()
            if cp.workflow_id == workflow_id and not cp.is_expired()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda cp: cp.created_at)

    async def list_by_workflow(
        self,
        workflow_id: str,
    ) -> list[Checkpoint]:
        candidates = [
            cp
            for cp in self._store.values()
            if cp.workflow_id == workflow_id and not cp.is_expired()
        ]
        return sorted(candidates, key=lambda cp: cp.created_at)

    async def delete(self, checkpoint_id: str) -> bool:
        if checkpoint_id not in self._store:
            return False
        del self._store[checkpoint_id]
        return True

    async def delete_by_workflow(self, workflow_id: str) -> int:
        to_delete = [
            cid
            for cid, cp in self._store.items()
            if cp.workflow_id == workflow_id
        ]
        for cid in to_delete:
            del self._store[cid]
        return len(to_delete)
