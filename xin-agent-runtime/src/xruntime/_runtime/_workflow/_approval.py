# -*- coding: utf-8 -*-
"""P3-B: Workflow HITL — ApprovalStep + ApprovalStore.

Implements human-in-the-loop approval gating at the workflow step
level.  When the orchestrator reaches an :class:`ApprovalStep`, it:

1. Creates an :class:`ApprovalRequest` in the :class:`ApprovalStore`.
2. Saves a checkpoint with ``WAITING_APPROVAL`` status.
3. Pauses the workflow (returns a sentinel indicating "waiting").
4. External approver submits decision via :meth:`ApprovalStore.submit_decision`.
5. Resume loads the checkpoint, reads the decision, and either
   continues (approved) or aborts (rejected / timed_out).

Design notes:

* :class:`ApprovalRequest` is a pydantic ``BaseModel`` so it
  serializes cleanly for Redis / DB storage.
* :class:`ApprovalStore` is an async ABC — production stores (Redis,
  Postgres) live in sibling modules.  :class:`InMemoryApprovalStore`
  is the test / dev default.
* Timeout is checked lazily via :meth:`check_timeout` — when called,
  the store checks ``created_at + timeout_seconds`` and, if expired,
  applies the ``on_timeout`` policy (``"reject"``, ``"approve"``,
  or ``"abort"``).
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field

from .._orchestrator import WorkflowStep

logger = logging.getLogger("xruntime.workflow.approval")


# Valid on_timeout policies.
_VALID_ON_TIMEOUT = {"reject", "approve", "abort"}

# Valid decision values.
_VALID_DECISIONS = {"pending", "approved", "rejected", "timed_out"}


# ── ApprovalRequest ─────────────────────────────────────────────


class ApprovalRequest(BaseModel):
    """A single approval request for a workflow step.

    Args:
        request_id (`str`):
            Unique identifier (auto-generated).
        workflow_id (`str`):
            The workflow this request belongs to.
        step_id (`str`):
            The :class:`ApprovalStep` that created this request.
        approver (`str`):
            The approver identifier (user_id / email / role).
        timeout_seconds (`int`):
            Timeout in seconds.  After this elapses, the request
            is auto-decided by :meth:`ApprovalStore.check_timeout`.
        decision (`str`):
            Current decision — ``"pending"`` (default),
            ``"approved"``, ``"rejected"``, or ``"timed_out"``.
        decided_by (`str`):
            Who made the decision (user_id).  Empty when pending.
        comment (`str`):
            Optional comment from the approver.
        created_at (`float`):
            Unix timestamp when the request was created.
        decided_at (`float | None`):
            Unix timestamp when the decision was made.
    """

    request_id: str = Field(
        default_factory=lambda: f"apr-{uuid.uuid4().hex[:12]}",
    )
    workflow_id: str
    step_id: str
    approver: str
    timeout_seconds: int = 3600
    decision: str = "pending"
    decided_by: str = ""
    comment: str = ""
    created_at: float = Field(default_factory=time.time)
    decided_at: float | None = None

    def is_pending(self) -> bool:
        """Return ``True`` if the request is still pending."""
        return self.decision == "pending"

    def is_approved(self) -> bool:
        """Return ``True`` if the request was approved."""
        return self.decision == "approved"

    def is_rejected(self) -> bool:
        """Return ``True`` if the request was rejected."""
        return self.decision == "rejected"

    def is_timed_out(self) -> bool:
        """Return ``True`` if the request timed out."""
        return self.decision == "timed_out"

    def has_expired(self, now: float | None = None) -> bool:
        """Return ``True`` if the timeout has elapsed.

        Args:
            now (`float | None`):
                Reference timestamp.  ``None`` uses ``time.time()``.

        Returns:
            `bool`: ``True`` if expired.
        """
        if now is None:
            now = time.time()
        return now >= (self.created_at + self.timeout_seconds)

    def is_resolved(self) -> bool:
        """Return ``True`` if the request has a final decision."""
        return self.decision in ("approved", "rejected", "timed_out")


# ── ApprovalStore ABC ─────────────────────────────────────────


class ApprovalStore:
    """Abstract base class for approval storage backends.

    All methods are async — backends may be network-bound (Redis,
    Postgres).  The in-memory implementation is in
    :class:`InMemoryApprovalStore`.
    """

    async def create_request(
        self,
        workflow_id: str,
        step_id: str,
        approver: str,
        timeout_seconds: int = 3600,
    ) -> ApprovalRequest:
        """Create and persist a new approval request.

        Args:
            workflow_id (`str`): The workflow id.
            step_id (`str`): The approval step id.
            approver (`str`): The approver identifier.
            timeout_seconds (`int`): Timeout in seconds.

        Returns:
            `ApprovalRequest`: The created request.
        """
        raise NotImplementedError

    async def get_request(
        self,
        request_id: str,
    ) -> ApprovalRequest | None:
        """Load a request by id.

        Args:
            request_id (`str`): The request id.

        Returns:
            `ApprovalRequest | None`: The request, or ``None``.
        """
        raise NotImplementedError

    async def submit_decision(
        self,
        request_id: str,
        *,
        decision: str,
        user_id: str,
        comment: str = "",
    ) -> None:
        """Submit a decision for a pending request.

        Args:
            request_id (`str`): The request id.
            decision (`str`): ``"approved"`` or ``"rejected"``.
            user_id (`str`): Who made the decision.
            comment (`str`): Optional comment.

        Raises:
            `KeyError`: If the request id is unknown.
            `ValueError`: If the decision is invalid.
            `RuntimeError`: If the request is already decided.
        """
        raise NotImplementedError

    async def list_pending(
        self,
        approver: str,
    ) -> list[ApprovalRequest]:
        """List all pending requests for an approver.

        Args:
            approver (`str`): The approver identifier.

        Returns:
            `list[ApprovalRequest]`: Pending requests.
        """
        raise NotImplementedError

    async def list_by_workflow(
        self,
        workflow_id: str,
    ) -> list[ApprovalRequest]:
        """List all requests for a workflow.

        Args:
            workflow_id (`str`): The workflow id.

        Returns:
            `list[ApprovalRequest]`: All requests for the workflow.
        """
        raise NotImplementedError

    async def check_timeout(
        self,
        request_id: str,
    ) -> bool:
        """Check if a request has timed out; if so, apply policy.

        Args:
            request_id (`str`): The request id.

        Returns:
            `bool`: ``True`` if the request was timed out (and
            auto-decided), ``False`` otherwise.
        """
        raise NotImplementedError


# ── InMemoryApprovalStore ──────────────────────────────────────


class InMemoryApprovalStore(ApprovalStore):
    """Dict-backed in-memory approval store.

    Suitable for tests, dev runs, and single-process workflows.
    Not suitable for production (no cross-process sharing).

    The default ``on_timeout`` policy for auto-timed-out requests is
    ``"timed_out"`` — callers inspecting the decision should check
    :meth:`ApprovalRequest.is_timed_out`.
    """

    def __init__(self) -> None:
        """Initialize the store."""
        self._store: dict[str, ApprovalRequest] = {}

    async def create_request(
        self,
        workflow_id: str,
        step_id: str,
        approver: str,
        timeout_seconds: int = 3600,
    ) -> ApprovalRequest:
        """Create and store a new approval request."""
        req = ApprovalRequest(
            workflow_id=workflow_id,
            step_id=step_id,
            approver=approver,
            timeout_seconds=timeout_seconds,
        )
        self._store[req.request_id] = req
        return req

    async def get_request(
        self,
        request_id: str,
    ) -> ApprovalRequest | None:
        """Load a request by id."""
        return self._store.get(request_id)

    async def submit_decision(
        self,
        request_id: str,
        *,
        decision: str,
        user_id: str,
        comment: str = "",
    ) -> None:
        """Submit a decision for a pending request."""
        if decision not in ("approved", "rejected"):
            raise ValueError(
                f"Invalid decision {decision!r}; must be "
                f"'approved' or 'rejected'",
            )
        req = self._store.get(request_id)
        if req is None:
            raise KeyError(f"Unknown request_id: {request_id}")
        if not req.is_pending():
            raise RuntimeError(
                f"Request {request_id} is already decided: " f"{req.decision}",
            )
        req.decision = decision
        req.decided_by = user_id
        req.comment = comment
        req.decided_at = time.time()

    async def list_pending(
        self,
        approver: str,
    ) -> list[ApprovalRequest]:
        """List all pending requests for an approver."""
        return [
            req
            for req in self._store.values()
            if req.approver == approver and req.is_pending()
        ]

    async def list_by_workflow(
        self,
        workflow_id: str,
    ) -> list[ApprovalRequest]:
        """List all requests for a workflow."""
        return [
            req
            for req in self._store.values()
            if req.workflow_id == workflow_id
        ]

    async def check_timeout(
        self,
        request_id: str,
    ) -> bool:
        """Check if a request has timed out; if so, mark it."""
        req = self._store.get(request_id)
        if req is None:
            return False
        if not req.is_pending():
            return False
        if not req.has_expired():
            return False
        # Mark as timed_out
        req.decision = "timed_out"
        req.decided_by = "system"
        req.decided_at = time.time()
        return True


# ── ApprovalStep ───────────────────────────────────────────────


@dataclass
class ApprovalStep(WorkflowStep):
    """A workflow step that pauses for human approval.

    When the orchestrator reaches this step, it creates an
    :class:`ApprovalRequest` in the configured :class:`ApprovalStore`
    and pauses the workflow.  Resume checks the decision and either
    continues (approved) or aborts (rejected / timed_out).

    Args:
        id (`str`): Unique step identifier.
        name (`str`): Human-readable step name.
        agent (`str`): Agent name (unused — this step only waits).
        prompt (`str`): Prompt (unused; kept for compatibility).
        approver (`str`):
            The approver identifier (user_id / email / role).
        timeout_seconds (`int`):
            Approval timeout.  After this elapses, ``on_timeout``
            policy is applied.
        on_timeout (`str`):
            Timeout policy — ``"reject"`` (default, fail-closed),
            ``"approve"`` (auto-approve), or ``"abort"`` (abort
            workflow).
        depends_on (`list[str]`):
            Step ids that must complete first.
        on_failure (`str`): Failure strategy (rarely used for
            approval steps — approval failure is governed by
            ``on_timeout``).
        max_retries (`int`): Max retries (unused).
    """

    approver: str = ""
    timeout_seconds: int = 3600
    on_timeout: str = "reject"

    def __post_init__(self) -> None:
        """Validate on_timeout policy at construction time."""
        if self.on_timeout not in _VALID_ON_TIMEOUT:
            raise ValueError(
                f"Invalid on_timeout {self.on_timeout!r}; must be "
                f"one of {_VALID_ON_TIMEOUT}",
            )


__all__ = [
    "ApprovalRequest",
    "ApprovalStore",
    "InMemoryApprovalStore",
    "ApprovalStep",
]
