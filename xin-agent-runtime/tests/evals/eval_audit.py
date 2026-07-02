# -*- coding: utf-8 -*-
"""Audit logging evals — verify middleware audit trail.

These evals verify that the EvalContext DSL can correctly assert
audit log entries.  They use a stub runner that simulates the
AuditMiddleware, so they run fully offline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from xruntime._eval import define_eval
from xruntime._eval._models import AssertionResult


@dataclass
class _AuditEntry:
    """Simulated audit log entry."""

    tenant_id: str
    user_id: str
    tool_name: str
    action: str
    timestamp: str = "2026-07-01T00:00:00Z"


class _AuditStubRunner:
    """Stub runner that simulates AuditMiddleware."""

    def __init__(self, entries: list[_AuditEntry] | None = None) -> None:
        self._reply = "ok"
        self._events: list[dict] = [
            {
                "type": "TOOL_CALL",
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/test.txt"},
            },
        ]
        self._entries = entries or []

    async def send(self, **kwargs: Any) -> tuple[str, list[dict]]:
        return self._reply, self._events

    def audit_entries(self, _tenant: str) -> list:
        return self._entries

    def scan_tenant_keys(self, _tenant: str) -> list:
        return []

    def approval_state_snapshot(self, _session: str) -> set:
        return set()


@define_eval(
    "Read tool call is recorded in audit log",
    domain="observability",
    tags=("offline",),
)
async def read_is_audit_logged(t: Any) -> None:
    """A Read tool call must produce an audit log entry."""
    t._runner = _AuditStubRunner(
        entries=[
            _AuditEntry(
                tenant_id="eval-test-tenant",
                user_id="eval-user",
                tool_name="Read",
                action="execute",
            ),
        ],
    )
    await t.send("Read /tmp/test.txt")
    t.audit_logged("Read")


@define_eval(
    "Missing audit entry is detected as failure",
    domain="observability",
    tags=("offline",),
)
async def missing_audit_detected(t: Any) -> None:
    """When an audit entry is missing, the DSL must record a failed
    assertion.  This eval verifies the recording mechanism itself."""
    t._runner = _AuditStubRunner(entries=[])
    await t.send("Read /tmp/test.txt")
    # Call audit_logged but expect it to fail — then verify the
    # failure was recorded WITHOUT causing this eval to fail.
    t.audit_logged("Read")
    # Pop the expected failure, verify it exists, then record a
    # single PASS assertion confirming the mechanism works.
    audit_assertions = [a for a in t._results if a.name == "audit_logged:Read"]
    t._results.clear()  # Remove the expected failure
    has_failure = len(audit_assertions) == 1 and not audit_assertions[0].passed
    t._results.append(
        AssertionResult(
            name="missing_audit_failure_recorded",
            passed=has_failure,
            message="missing audit should be recorded as failure",
            evidence={"recorded_assertion": str(audit_assertions)},
        ),
    )
