# -*- coding: utf-8 -*-
"""Approval middleware evals — verify HITL approval gating.

These evals verify that the EvalContext DSL can correctly assert
approval request events and approval state caching.  They use a
stub runner that simulates the ApprovalMiddleware, so they run
fully offline.
"""
from __future__ import annotations

from typing import Any

from xruntime._eval import define_eval
from xruntime._eval._models import AssertionResult


class _ApprovalStubRunner:
    """Stub runner that simulates ApprovalMiddleware behavior."""

    def __init__(
        self,
        events: list[dict] | None = None,
        cached_tools: set[str] | None = None,
    ) -> None:
        self._reply = "Tool executed"
        self._events = events or []
        self._cached = cached_tools or set()

    async def send(self, **kwargs: Any) -> tuple[str, list[dict]]:
        return self._reply, self._events

    def audit_entries(self, _tenant: str) -> list:
        return []

    def scan_tenant_keys(self, _tenant: str) -> list:
        return []

    def approval_state_snapshot(self, _session: str) -> set[str]:
        return self._cached


@define_eval(
    "Write tool requires approval under always strategy",
    domain="security",
    tags=("offline",),
)
async def write_requires_approval(t: Any) -> None:
    """The Write tool must trigger an APPROVAL_REQUEST event."""
    t._runner = _ApprovalStubRunner(
        events=[
            {
                "type": "APPROVAL_REQUEST",
                "tool_name": "Write",
                "tool_input": {"file_path": "/etc/passwd"},
            },
        ],
    )
    await t.send("Write to /etc/passwd")
    t.approval_required_for("Write")


@define_eval(
    "ONCE strategy caches approval for subsequent calls",
    domain="security",
    tags=("offline",),
)
async def once_strategy_caches_approval(t: Any) -> None:
    """After first approval, the ONCE strategy should cache the
    decision so subsequent calls skip the approval prompt."""
    t._runner = _ApprovalStubRunner(
        cached_tools={"Write"},
    )
    t.as_session("sess-once-test")
    await t.send("Write to a file")
    t.approval_was_cached("Write")


@define_eval(
    "Read tool does NOT require approval under default config",
    domain="security",
    tags=("offline",),
)
async def read_does_not_require_approval(t: Any) -> None:
    """The Read tool should not trigger an APPROVAL_REQUEST event
    when not listed in always_require_tools."""
    t._runner = _ApprovalStubRunner(events=[])
    await t.send("Read a file")
    # Verify no APPROVAL_REQUEST event was emitted
    has_approval_request = any(
        e.get("type") == "APPROVAL_REQUEST" for e in t.events
    )
    t._results.append(
        AssertionResult(
            name="no_approval_for_read",
            passed=not has_approval_request,
            message="Read tool should not trigger approval",
        ),
    )
