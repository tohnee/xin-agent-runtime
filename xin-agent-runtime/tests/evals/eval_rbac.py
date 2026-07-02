# -*- coding: utf-8 -*-
"""RBAC security evals — verify role-based access control.

These evals verify that the EvalContext DSL can correctly assert
RBAC denial events.  They use a stub runner that simulates
middleware deny events, so they run fully offline.
"""
from __future__ import annotations

from typing import Any

from xruntime._eval import define_eval
from xruntime._eval._models import AssertionResult


class _RbacStubRunner:
    """Stub runner that simulates RBAC deny on Write tool."""

    def __init__(self) -> None:
        self._reply = (
            "Permission denied: Write tool not allowed for viewer role"
        )
        self._events = [
            {
                "type": "MIDDLEWARE_DENY",
                "middleware": "rbac",
                "tool_name": "Write",
                "reason": "viewer role cannot use Write",
            },
        ]

    async def send(self, **kwargs: Any) -> tuple[str, list[dict]]:
        return self._reply, self._events

    def audit_entries(self, _tenant: str) -> list:
        return []

    def scan_tenant_keys(self, _tenant: str) -> list:
        return []

    def approval_state_snapshot(self, _session: str) -> set:
        return set()


@define_eval(
    "Viewer role is blocked from Write tool by RBAC middleware",
    domain="security",
    tags=("offline",),
)
async def viewer_blocked_from_write(t: Any) -> None:
    """A viewer-role agent must be denied Write tool access."""
    t._runner = _RbacStubRunner()
    await t.send("Write a file to /etc/passwd")
    t.expect_blocked(by="rbac")
    t.reply_contains("denied")


@define_eval(
    "Owner role is NOT blocked by RBAC (no deny event)",
    domain="security",
    tags=("offline",),
)
async def owner_not_blocked(t: Any) -> None:
    """An owner-role agent should not see RBAC deny events."""
    t._runner = _RbacStubRunner()
    t._runner._events = []  # No deny events for owner
    t._runner._reply = "File written successfully"
    await t.send("Write a file")
    # Manually assert no deny event
    has_deny = any(e.get("type") == "MIDDLEWARE_DENY" for e in t.events)
    t._results.append(
        AssertionResult(
            name="no_rbac_deny_for_owner",
            passed=not has_deny,
            message=f"unexpected deny event: {t.events}",
        ),
    )
    t.reply_contains("successfully")
