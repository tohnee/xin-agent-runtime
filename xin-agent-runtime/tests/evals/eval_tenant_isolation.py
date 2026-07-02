# -*- coding: utf-8 -*-
"""Tenant isolation evals — verify multi-tenant storage isolation.

These evals verify that the EvalContext DSL can correctly detect
cross-tenant key leaks.  They use a stub runner that simulates
fakeredis key scanning, so they run fully offline.
"""
from __future__ import annotations

from typing import Any

from xruntime._eval import define_eval
from xruntime._eval._models import AssertionResult


class _TenantStubRunner:
    """Stub runner that simulates tenant key scanning."""

    def __init__(self, leaked_keys: list[str] | None = None) -> None:
        self._reply = "ok"
        self._events: list[dict] = []
        self._leaked = leaked_keys or []

    async def send(self, **kwargs: Any) -> tuple[str, list[dict]]:
        return self._reply, self._events

    def audit_entries(self, _tenant: str) -> list:
        return []

    def scan_tenant_keys(self, _tenant: str) -> list[str]:
        return self._leaked

    def approval_state_snapshot(self, _session: str) -> set:
        return set()


@define_eval(
    "Tenant A storage keys do not leak into Tenant B namespace",
    domain="security",
    tags=("offline",),
)
async def no_cross_tenant_leak(t: Any) -> None:
    """No storage key with tenant:B: prefix should exist when
    only tenant:A has been active."""
    t._runner = _TenantStubRunner(leaked_keys=[])
    t.as_tenant("tenant-alpha")
    await t.send("Read some data")
    t.no_cross_tenant_leak("tenant-beta")


@define_eval(
    "Cross-tenant leak is detected and reported as failure",
    domain="security",
    tags=("offline",),
)
async def leak_is_detected(t: Any) -> None:
    """When a key leak exists, the DSL must record a failed assertion
    with the leaked keys as evidence.  This eval verifies the detection
    mechanism itself."""
    t._runner = _TenantStubRunner(
        leaked_keys=["tenant:beta:session:xyz"],
    )
    t.as_tenant("tenant-alpha")
    await t.send("Read some data")
    # Call no_cross_tenant_leak but expect it to fail — then verify
    # the failure was recorded WITHOUT causing this eval to fail.
    t.no_cross_tenant_leak("tenant-beta")
    leak_assertions = [
        a for a in t._results if a.name == "no_leak_to:tenant-beta"
    ]
    t._results.clear()  # Remove the expected failure
    has_failure = (
        len(leak_assertions) == 1
        and not leak_assertions[0].passed
        and "tenant:beta:session:xyz" in leak_assertions[0].message
    )
    t._results.append(
        AssertionResult(
            name="leak_failure_was_recorded",
            passed=has_failure,
            message="leak assertion should have been recorded as failure",
            evidence={"recorded_assertion": str(leak_assertions)},
        ),
    )
