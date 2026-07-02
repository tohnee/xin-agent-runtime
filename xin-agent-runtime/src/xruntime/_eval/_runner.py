# -*- coding: utf-8 -*-
"""EvalRunner — collect, execute, and report evals.

The runner orchestrates the full eval lifecycle:

1. :class:`EvalCollector` scans ``tests/evals/`` for specs.
2. For each spec, an :class:`EvalContext` is created and the spec's
   async function is awaited.
3. Assertion results are aggregated into an :class:`EvalResult`.
4. Reporters render the results.

Status decision:

* ``PASSED`` — all assertions passed, no exception.
* ``FAILED`` — one or more assertions failed, no exception.
* ``ERROR`` — uncaught exception during eval execution.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from ._collector import EvalCollector
from ._context import EvalContext
from ._models import AssertionResult, EvalResult, EvalSpec, EvalStatus
from ._reporter import (
    ConsoleReporter,
    JsonReporter,
    JUnitReporter,
    Reporter,
)
from ._target_inproc import InProcessTarget
from ._target_remote import RemoteTarget


class EvalRunner:
    """Collect and run evals against an in-process or remote target.

    Args:
        target (`str | None`):
            ``"in-process"`` (default) or a URL.  Falls back to the
            ``XRUNTIME_EVAL_TARGET`` env var.
        tags (`list[str] | None`):
            Only run evals whose tags intersect this list.
            Defaults to ``["offline"]``.
        reporters (`list[Reporter] | None`):
            Output reporters.  Defaults to Console + JUnit + JSON.
    """

    def __init__(
        self,
        target: str | None = None,
        *,
        tags: list[str] | None = None,
        reporters: list[Reporter] | None = None,
    ) -> None:
        self.target = target or os.environ.get(
            "XRUNTIME_EVAL_TARGET",
            "in-process",
        )
        self.tags = tags or ["offline"]
        self.reporters = reporters or [
            ConsoleReporter(),
            JUnitReporter(path="eval-results.xml"),
            JsonReporter(path="eval-results.json"),
        ]
        self._target_obj: Any = None

    async def run(self, evals_dir: str = "tests/evals") -> int:
        """Run collected evals; return exit code (0 = pass).

        Args:
            evals_dir (`str`): Directory to scan for eval specs.

        Returns:
            `int`: 0 if all evals passed, 1 otherwise.
        """
        specs = EvalCollector(evals_dir).collect(tags=self.tags)
        results: list[EvalResult] = []
        for spec in specs:
            result = await self._run_one(spec)
            results.append(result)
        for r in self.reporters:
            r.report(results)
        return 0 if all(r.status == EvalStatus.PASSED for r in results) else 1

    async def _run_one(self, spec: EvalSpec) -> EvalResult:
        """Execute a single eval; capture status and trace.

        Args:
            spec (`EvalSpec`): The eval to run.

        Returns:
            `EvalResult`: The aggregate result.
        """
        ctx = EvalContext(self, spec.eval_id)
        status = EvalStatus.PASSED
        trace: dict[str, Any] = {}
        start = time.monotonic()
        try:
            await self._setup_target()
            await spec.fn(ctx)
            if any(not a.passed for a in ctx.results):
                status = EvalStatus.FAILED
            trace = self._capture_trace()
        except Exception as exc:  # noqa: BLE001
            status = EvalStatus.ERROR
            trace["exception"] = repr(exc)
            # Ensure the exception is recorded as an assertion so it
            # shows up in the report.
            if not any(
                a.name == "__uncaught_exception__" for a in ctx.results
            ):
                ctx._results.append(
                    AssertionResult(
                        name="__uncaught_exception__",
                        passed=False,
                        message=repr(exc),
                    ),
                )

        duration_ms = int((time.monotonic() - start) * 1000)
        return EvalResult(
            eval_id=spec.eval_id,
            description=spec.description,
            status=status,
            assertions=ctx.results,
            trace=trace,
            duration_ms=duration_ms,
        )

    # ── target lifecycle ────────────────────────────────────────

    async def _setup_target(self) -> None:
        """Lazily set up the target (in-process or remote).

        Called once per eval to ensure the target is ready.  In
        practice the target is cached after the first call.
        """
        if self._target_obj is not None:
            return
        if self.target == "in-process":
            self._target_obj = InProcessTarget()
        else:
            self._target_obj = RemoteTarget(base_url=self.target)
        await self._target_obj.setup()

    def _capture_trace(self) -> dict[str, Any]:
        """Snapshot the current target state for debugging.

        Returns:
            `dict`: A trace dict (empty in MVP; extended in Phase 3).
        """
        return {}

    # ── transport (called by EvalContext.send) ──────────────────

    async def send(
        self,
        *,
        tenant_id: str,
        role: str,
        message: str,
    ) -> tuple[str, list[dict]]:
        """Delegate to the target's ``send`` method.

        Args:
            tenant_id (`str`): The tenant id.
            role (`str`): The user role.
            message (`str`): The user message.

        Returns:
            `tuple[str, list[dict]]`: The reply text and event list.
        """
        if self._target_obj is None:
            # Fallback for unit tests that mock _setup_target.
            return "", []
        return await self._target_obj.send(
            tenant_id=tenant_id,
            role=role,
            message=message,
        )

    def audit_entries(self, tenant_id: str) -> list[Any]:
        """Delegate to the target's ``audit_entries`` method.

        Args:
            tenant_id (`str`): The tenant id.

        Returns:
            `list`: Audit entries.
        """
        if self._target_obj is None:
            return []
        return self._target_obj.audit_entries(tenant_id)

    def scan_tenant_keys(self, tenant_id: str) -> list[str]:
        """Delegate to the target's ``scan_tenant_keys`` method.

        Args:
            tenant_id (`str`): The tenant id.

        Returns:
            `list[str]`: Leaked keys.
        """
        if self._target_obj is None:
            return []
        return self._target_obj.scan_tenant_keys(tenant_id)

    def approval_state_snapshot(self, session_id: str) -> set[str]:
        """Delegate to the target's ``approval_state_snapshot`` method.

        Args:
            session_id (`str`): The session id.

        Returns:
            `set[str]`: Approved tool names.
        """
        if self._target_obj is None:
            return set()
        return self._target_obj.approval_state_snapshot(session_id)
