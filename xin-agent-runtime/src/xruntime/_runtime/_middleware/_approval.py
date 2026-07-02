# -*- coding: utf-8 -*-
"""Approval middleware — Human-in-the-Loop (HITL) approval gating.

Intercepts every tool call and, depending on the configured strategy,
asks an external approver (e.g. a UI / slack / webhook) to explicitly
allow or deny the call before it reaches ``_acting_impl``.

Strategies
----------

* ``always``   — every tool call blocks until the approver responds.
* ``once``     — the first call of a tool in a session blocks; subsequent
                 calls of the same tool name in the same session are
                 auto-approved.  Approval state is shared across turns
                 via :class:`ApprovalStateCache`.
* ``never``    — passthrough; never asks for approval.  Convenience
                 for disabling the middleware without removing it from
                 the chain.
* ``predicate``— asks for approval only when a caller-supplied
                 predicate ``(tool_name, tool_input) -> bool`` returns
                 ``True``.

Overrides
---------

Two tool-name sets provide per-tool overrides that take precedence
over the strategy:

* ``always_require_tools`` — always ask for approval, even when the
  strategy is ``never``.
* ``never_require_tools``  — never ask for approval, even when the
  strategy is ``always``.

Denial / timeout
----------------

* An explicit rejection (``ApprovalDecision(approved=False)``) raises
  :class:`PermissionError`, mirroring :class:`RbacMiddleware` so the
  audit middleware records ``decision="DENY"``.
* A timed-out approval raises :class:`ApprovalTimeoutError` (subclass
  of :class:`RuntimeError`) so the gateway can map it to a 408.

Inherits :class:`agentscope.middleware.MiddlewareBase` so the AS
Agent middleware system correctly detects the ``on_acting`` hook.
"""
from __future__ import annotations

import asyncio
import enum
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Awaitable, Callable

from pydantic import BaseModel, Field

from agentscope.middleware import MiddlewareBase

logger = logging.getLogger(__name__)


# ── public types ────────────────────────────────────────────────────


class ApprovalStrategy(str, enum.Enum):
    """Approval strategies.

    Values:
        ALWAYS: Every tool call requires human approval.
        ONCE: First call of a tool in a session requires approval;
            subsequent calls of the same tool name are auto-approved.
        NEVER: Passthrough; no approval required.
        PREDICATE: Approval required when the predicate returns ``True``.
    """

    ALWAYS = "always"
    ONCE = "once"
    NEVER = "never"
    PREDICATE = "predicate"


@dataclass
class ApprovalDecision:
    """The approver's verdict for a single tool call.

    Args:
        approved (`bool`):
            Whether the tool call may proceed.
        reason (`str`):
            Human-readable reason for the decision (logged on deny).
        approver (`str`):
            Identifier of the human / system that issued the decision.
    """

    approved: bool
    reason: str = ""
    approver: str = ""


@dataclass
class ApprovalRequest:
    """Payload handed to the approver callback.

    Args:
        request_id (`str`):
            Unique id for this approval request (UUID4 hex).
        session_id (`str`):
            The agent session id.
        tenant_id (`str`):
            Tenant id.
        user_id (`str`):
            User id.
        tool_name (`str`):
            Name of the tool about to be called.
        tool_input (`dict`):
            Tool input arguments (already redacted if a redaction
            middleware runs earlier in the chain).
    """

    request_id: str
    session_id: str
    tenant_id: str
    user_id: str
    tool_name: str
    tool_input: dict[str, Any]


# Callable signature: async (ApprovalRequest) -> ApprovalDecision
ApprovalCallback = Callable[
    [ApprovalRequest],
    Awaitable[ApprovalDecision],
]

# Callable signature: (tool_name, tool_input) -> bool
PredicateCallback = Callable[[str, dict[str, Any]], bool]


class ApprovalTimeoutError(RuntimeError):
    """Raised when the approver does not respond within the timeout.

    Subclass of :class:`RuntimeError` (not :class:`PermissionError`)
    so the gateway can distinguish "no response" from "explicit deny"
    and map it to HTTP 408 instead of 403.
    """


# ── config ──────────────────────────────────────────────────────────


def _to_set(value: Any) -> set[str]:
    """Coerce ``list[str] | set[str] | None`` to ``set[str]``.

    Args:
        value (`Any`): The input value.

    Returns:
        `set[str]`: The coerced set.
    """
    if value is None:
        return set()
    if isinstance(value, set):
        return value
    if isinstance(value, (list, tuple)):
        return set(value)
    return set()


class ApprovalConfig(BaseModel):
    """Approval middleware configuration.

    Pydantic model — lives on :class:`XRuntimeConfig.approval` so it
    can be loaded from YAML / env overrides the same way as the other
    XRuntime config sections.

    Args:
        enabled (`bool`):
            Whether the ApprovalMiddleware is attached to the chain.
            ``False`` (default) keeps the middleware out of the chain
            entirely so there is zero overhead when HITL is not used.
        strategy (`ApprovalStrategy`):
            The approval strategy.  Defaults to ``NEVER`` so flipping
            ``enabled=True`` alone does not start blocking tool calls.
        timeout_seconds (`float`):
            How long to wait for the approver before raising
            :class:`ApprovalTimeoutError`.  Defaults to 300s (5 min).
        always_require_tools (`list[str]`):
            Tool names that always require approval regardless of
            the strategy.  Stored as a list for pydantic
            serializability; converted to ``set`` at middleware
            construction time.
        never_require_tools (`list[str]`):
            Tool names that never require approval regardless of
            the strategy.
    """

    enabled: bool = False
    strategy: ApprovalStrategy = ApprovalStrategy.NEVER
    timeout_seconds: float = 300.0
    always_require_tools: list[str] = Field(default_factory=list)
    never_require_tools: list[str] = Field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"ApprovalConfig(enabled={self.enabled}, "
            f"strategy={self.strategy.value}, "
            f"timeout_seconds={self.timeout_seconds})"
        )


# ── state cache (cross-turn sharing for ONCE) ──────────────────────


class ApprovalStateCache:
    """Per-session record of tools that have already been approved.

    Used by the ``once`` strategy so a tool approved in turn 1 does
    not block again in turn 2 of the same session.  State is keyed
    by ``(session_id, tool_name)`` and lives in-process — sufficient
    for a single-node deployment.  Multi-node deployments would need
    a Redis-backed implementation; that is deferred to P2.

    The cache is async-safe via a per-session :class:`asyncio.Lock`
    (one lock per ``session_id``, lazily created in ``_locks``) so
    concurrent tool calls within the same session do not race, while
    calls on *different* sessions do not serialize against each
    other.  ``clear_session`` also drops the session's lock entry to
    keep the ``_locks`` dict bounded across session lifetimes.
    """

    def __init__(self) -> None:
        self._approved: dict[str, set[str]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        """Return the :class:`asyncio.Lock` for ``session_id``.

        Lazily creates the lock on first access.  Safe under the
        asyncio single-threaded event loop (no ``await`` between
        the membership check and the assignment).

        Args:
            session_id (`str`): The session id.

        Returns:
            `asyncio.Lock`: The per-session lock.
        """
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    async def mark_approved(
        self,
        session_id: str,
        tool_name: str,
    ) -> None:
        """Record that ``tool_name`` was approved in ``session_id``.

        Args:
            session_id (`str`): The session id.
            tool_name (`str`): The tool name.
        """
        async with self._get_lock(session_id):
            self._approved.setdefault(session_id, set()).add(tool_name)

    async def is_approved(
        self,
        session_id: str,
        tool_name: str,
    ) -> bool:
        """Return ``True`` if ``tool_name`` was already approved.

        Args:
            session_id (`str`): The session id.
            tool_name (`str`): The tool name.

        Returns:
            `bool`: Whether the tool was previously approved.
        """
        async with self._get_lock(session_id):
            return tool_name in self._approved.get(session_id, set())

    async def clear_session(self, session_id: str) -> None:
        """Drop all approval records for a session.

        Also removes the per-session lock entry so the ``_locks``
        dict does not grow unboundedly across session lifetimes.

        Args:
            session_id (`str`): The session id.
        """
        async with self._get_lock(session_id):
            self._approved.pop(session_id, None)
            self._locks.pop(session_id, None)


# ── middleware ──────────────────────────────────────────────────────


class ApprovalMiddleware(MiddlewareBase):
    """Middleware that gates tool calls behind human approval.

    Args:
        strategy (`ApprovalStrategy`):
            The approval strategy.
        approval_callback (`ApprovalCallback | None`):
            Async callable invoked when approval is required.  ``None``
            means no approver is wired — any strategy that requires
            approval will time out (useful for tests).
        timeout_seconds (`float`):
            Seconds to wait for the approver.
        tenant_id (`str`):
            Tenant id (recorded in the approval request).
        user_id (`str`):
            User id (recorded in the approval request).
        predicate (`PredicateCallback | None`):
            Required for ``PREDICATE`` strategy.
        always_require_tools (`set[str] | None`):
            Tools that always require approval regardless of strategy.
        never_require_tools (`set[str] | None`):
            Tools that never require approval regardless of strategy.
        state_cache (`ApprovalStateCache | None`):
            Shared cache used by ``ONCE`` strategy.  When ``None``,
            a fresh cache is created (resets every turn — usually
            you want to pass the shared cache from
            :class:`MiddlewareStateCache`).
    """

    def __init__(
        self,
        strategy: ApprovalStrategy = ApprovalStrategy.NEVER,
        approval_callback: ApprovalCallback | None = None,
        timeout_seconds: float = 300.0,
        tenant_id: str = "default",
        user_id: str = "anonymous",
        predicate: PredicateCallback | None = None,
        always_require_tools: set[str] | None = None,
        never_require_tools: set[str] | None = None,
        state_cache: ApprovalStateCache | None = None,
    ) -> None:
        self.strategy = strategy
        self.approval_callback = approval_callback
        self.timeout_seconds = timeout_seconds
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.predicate = predicate
        self.always_require_tools = always_require_tools or set()
        self.never_require_tools = never_require_tools or set()
        self.state_cache = state_cache or ApprovalStateCache()

    # ── helpers ──────────────────────────────────────────────────

    def _requires_approval(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        session_id: str,
    ) -> bool:
        """Decide whether this tool call needs human approval.

        Override precedence (highest first):
        1. ``never_require_tools`` → False
        2. ``always_require_tools`` → True
        3. Strategy

        Args:
            tool_name (`str`): The tool name.
            tool_input (`dict`): The tool input.
            session_id (`str`): The session id (unused here, kept for
                symmetry with the ``once`` strategy's cache check).

        Returns:
            `bool`: Whether approval is required.
        """
        if tool_name in self.never_require_tools:
            return False
        if tool_name in self.always_require_tools:
            return True

        if self.strategy is ApprovalStrategy.NEVER:
            return False
        if self.strategy is ApprovalStrategy.ALWAYS:
            return True
        if self.strategy is ApprovalStrategy.PREDICATE:
            if self.predicate is None:
                raise ValueError(
                    "ApprovalStrategy.PREDICATE requires a predicate "
                    "callback; pass `predicate=` to ApprovalMiddleware.",
                )
            return bool(self.predicate(tool_name, tool_input))
        if self.strategy is ApprovalStrategy.ONCE:
            # The cache check happens in on_acting (async) — here we
            # only say "yes, the strategy wants approval".  on_acting
            # will skip the approver call if the cache already has it.
            return True
        return False

    async def _ask_approver(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        session_id: str,
    ) -> ApprovalDecision:
        """Invoke the approver with a timeout.

        Args:
            tool_name (`str`): The tool name.
            tool_input (`dict`): The tool input.
            session_id (`str`): The session id.

        Returns:
            `ApprovalDecision`: The approver's verdict.

        Raises:
            ApprovalTimeoutError: If the approver does not respond
                within ``timeout_seconds``.
            ApprovalTimeoutError: If no approver is wired.
        """
        if self.approval_callback is None:
            # No approver — wait out the timeout so the caller can
            # observe the configured grace period before failing.
            await asyncio.sleep(self.timeout_seconds)
            raise ApprovalTimeoutError(
                f"Approval for tool '{tool_name}' timed out "
                f"({self.timeout_seconds}s) — no approver configured",
            )

        request = ApprovalRequest(
            request_id=uuid.uuid4().hex,
            session_id=session_id,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            tool_name=tool_name,
            tool_input=tool_input,
        )

        try:
            return await asyncio.wait_for(
                self.approval_callback(request),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise ApprovalTimeoutError(
                f"Approval for tool '{tool_name}' timed out "
                f"after {self.timeout_seconds}s",
            ) from exc

    # ── MiddlewareBase hook ─────────────────────────────────────

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Gate a tool call behind human approval.

        Args:
            agent (`Agent`):
                The agent instance.
            input_kwargs (`dict`):
                Contains ``tool_call`` with tool execution details.
            next_handler (`Callable`):
                The next middleware or ``_acting_impl``.

        Yields:
            Tool chunks from the tool execution.

        Raises:
            PermissionError: If the approver explicitly rejects the call.
            ApprovalTimeoutError: If the approver does not respond
                within ``timeout_seconds``.
        """
        tool_call = input_kwargs.get("tool_call")
        if tool_call is None:
            # No tool_call — passthrough (matches AuditMiddleware).
            async for chunk in next_handler():
                yield chunk
            return

        tool_name = getattr(tool_call, "name", "") or getattr(
            tool_call,
            "tool_call_name",
            "",
        )
        raw_input = getattr(tool_call, "input", {}) or {}
        # Parse JSON-string inputs (AS ToolCallBlock.input may be a
        # raw JSON string accumulated during streaming — matches
        # AuditMiddleware._redact_input).
        tool_input: dict[str, Any]
        if isinstance(raw_input, str):
            try:
                parsed = json.loads(raw_input)
                tool_input = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError):
                tool_input = {}
        elif isinstance(raw_input, dict):
            tool_input = raw_input
        else:
            tool_input = {}

        session_id = ""
        if hasattr(agent, "state") and hasattr(agent.state, "session_id"):
            session_id = agent.state.session_id or ""

        # ── decide whether approval is required ────────────────
        if not self._requires_approval(tool_name, tool_input, session_id):
            async for chunk in next_handler():
                yield chunk
            return

        # ── ONCE: short-circuit if already approved in this session
        if self.strategy is ApprovalStrategy.ONCE:
            if await self.state_cache.is_approved(session_id, tool_name):
                logger.debug(
                    "[APPROVAL-ONCE] session=%s tool=%s — already "
                    "approved, passthrough",
                    session_id,
                    tool_name,
                )
                async for chunk in next_handler():
                    yield chunk
                return

        # ── ask the approver ───────────────────────────────────
        decision = await self._ask_approver(
            tool_name,
            tool_input,
            session_id,
        )

        if not decision.approved:
            logger.warning(
                "[APPROVAL-DENIED] session=%s tool=%s reason=%s "
                "approver=%s",
                session_id,
                tool_name,
                decision.reason,
                decision.approver,
            )
            raise PermissionError(
                f"Approval denied for tool '{tool_name}' "
                f"in session '{session_id}': {decision.reason}",
            )

        logger.info(
            "[APPROVAL-GRANTED] session=%s tool=%s approver=%s",
            session_id,
            tool_name,
            decision.approver,
        )

        # ── ONCE: cache the approval so future calls skip ──────
        if self.strategy is ApprovalStrategy.ONCE:
            await self.state_cache.mark_approved(session_id, tool_name)

        # ── passthrough ────────────────────────────────────────
        async for chunk in next_handler():
            yield chunk
