# -*- coding: utf-8 -*-
"""EvalContext — the DSL handle passed into ``@define_eval`` functions.

Provides Eve-style ``send`` / ``check`` / ``called_tool`` plus
XRuntime-specific ``as_tenant`` / ``as_role`` / ``expect_blocked`` /
``audit_logged`` / ``approval_required_for``.

Core principle: assertions **never raise** — they record an
:class:`AssertionResult` to ``self._results``.  The Runner aggregates
these to decide PASSED / FAILED.  This lets a single eval run all
assertions even if earlier ones fail, giving richer failure info.
"""
from __future__ import annotations

from typing import Any

from ._matchers import Matcher, includes
from ._models import AssertionResult


class EvalContext:
    """Per-eval DSL handle passed into ``@define_eval`` functions.

    Args:
        runner (`Any`):
            The :class:`EvalRunner` (or stub) that owns this context.
            Used to delegate ``send`` / ``audit_entries`` /
            ``scan_tenant_keys``.
        eval_id (`str`):
            The unique id of this eval (``domain.name``).
    """

    def __init__(self, runner: Any, eval_id: str) -> None:
        self._runner = runner
        self._eval_id = eval_id
        self._results: list[AssertionResult] = []
        self._tenant_id: str = "eval-default-tenant"
        self._role: str = "viewer"
        self._session_id: str = "eval-default-session"
        self.reply: str = ""
        self.events: list[dict] = []

    # ── identity / scoping ──────────────────────────────────────

    def as_tenant(self, tenant_id: str) -> "EvalContext":
        """Act as ``tenant_id`` for subsequent sends.

        Args:
            tenant_id (`str`): The tenant id.

        Returns:
            `EvalContext`: ``self`` for chaining.
        """
        self._tenant_id = tenant_id
        return self

    def as_role(self, role: str) -> "EvalContext":
        """Act as ``role`` (owner/admin/contributor/viewer).

        Args:
            role (`str`): The role.

        Returns:
            `EvalContext`: ``self`` for chaining.
        """
        self._role = role
        return self

    def as_session(self, session_id: str) -> "EvalContext":
        """Act under ``session_id`` for approval-cache assertions.

        Args:
            session_id (`str`): The session id.

        Returns:
            `EvalContext`: ``self`` for chaining.
        """
        self._session_id = session_id
        return self

    # ── interaction ─────────────────────────────────────────────

    async def send(self, message: str) -> str:
        """Send a user turn; populate ``self.reply`` and ``self.events``.

        Args:
            message (`str`): The user message.

        Returns:
            `str`: The agent reply text.
        """
        self.reply, self.events = await self._runner.send(
            tenant_id=self._tenant_id,
            role=self._role,
            message=message,
        )
        return self.reply

    # ── assertions (non-raising) ────────────────────────────────

    def check(
        self,
        value: Any,
        matcher: Matcher,
        name: str = "",
    ) -> None:
        """Assert ``value`` satisfies ``matcher`` (Eve parity).

        Args:
            value (`Any`): The value to check.
            matcher (`Matcher`): The matcher.
            name (`str`): Optional assertion name override.
        """
        ok, msg = matcher.match(value)
        self._results.append(
            AssertionResult(
                name=name or matcher.__class__.__name__,
                passed=ok,
                message=msg,
                evidence={"value": str(value)[:500]},
            ),
        )

    def reply_contains(self, needle: str) -> None:
        """Shortcut: ``check(self.reply, includes(needle))``.

        Args:
            needle (`str`): The substring to look for in the reply.
        """
        self.check(self.reply, includes(needle), name="reply_contains")

    def reply_matches(self, pattern: str) -> None:
        """Shortcut: ``check(self.reply, matches_regex(pattern))``.

        Args:
            pattern (`str`): The regex pattern.
        """
        from ._matchers import matches_regex

        self.check(self.reply, matches_regex(pattern), name="reply_matches")

    def called_tool(
        self,
        name: str,
        *,
        times: int | None = None,
    ) -> None:
        """Assert a tool was called (optionally exact count).

        Args:
            name (`str`): The tool name.
            times (`int | None`): If given, assert exact call count.
        """
        calls = [
            e
            for e in self.events
            if e.get("type") == "TOOL_CALL" and e.get("tool_name") == name
        ]
        ok = len(calls) > 0 if times is None else len(calls) == times
        self._results.append(
            AssertionResult(
                name=f"called_tool:{name}",
                passed=ok,
                message=(
                    f"expected {times or '>=1'} call(s), got {len(calls)}"
                ),
                evidence={"calls": calls},
            ),
        )

    def tool_input_matches(
        self,
        name: str,
        matcher: Matcher,
    ) -> None:
        """Assert a tool call's input satisfies ``matcher``.

        Args:
            name (`str`): The tool name.
            matcher (`Matcher`): The matcher for the tool input dict.
        """
        calls = [
            e
            for e in self.events
            if e.get("type") == "TOOL_CALL" and e.get("tool_name") == name
        ]
        if not calls:
            self._results.append(
                AssertionResult(
                    name=f"tool_input_matches:{name}",
                    passed=False,
                    message="tool was never called",
                ),
            )
            return
        tool_input = calls[0].get("tool_input", {})
        ok, msg = matcher.match(tool_input)
        self._results.append(
            AssertionResult(
                name=f"tool_input_matches:{name}",
                passed=ok,
                message=msg,
                evidence={"tool_input": tool_input},
            ),
        )

    def expect_blocked(self, *, by: str = "rbac") -> None:
        """Assert the last send was blocked by a middleware.

        Args:
            by (`str`): ``rbac`` / ``quota`` / ``approval`` / ``redaction``.
        """
        blocked = any(
            e.get("type") == "MIDDLEWARE_DENY"
            and e.get("middleware", "").lower() == by.lower()
            for e in self.events
        )
        self._results.append(
            AssertionResult(
                name=f"expect_blocked_by:{by}",
                passed=blocked,
                message=f"no {by} deny event observed",
                evidence={"events": self.events},
            ),
        )

    def audit_logged(self, tool_name: str) -> None:
        """Assert ``tool_name`` appears in the audit log.

        Args:
            tool_name (`str`): The tool name to look for.
        """
        entries = self._runner.audit_entries(self._tenant_id)
        ok = any(
            getattr(e, "tool_name", "") == tool_name
            or e.get("tool_name", "") == tool_name
            for e in entries
        )
        self._results.append(
            AssertionResult(
                name=f"audit_logged:{tool_name}",
                passed=ok,
                message=f"no audit entry for {tool_name}",
                evidence={"entries": [str(e)[:200] for e in entries]},
            ),
        )

    def no_cross_tenant_leak(self, other_tenant: str) -> None:
        """Assert no storage key leaks into ``other_tenant``.

        Args:
            other_tenant (`str`): The tenant to scan for leaks.
        """
        leaked = self._runner.scan_tenant_keys(other_tenant)
        ok = len(leaked) == 0
        self._results.append(
            AssertionResult(
                name=f"no_leak_to:{other_tenant}",
                passed=ok,
                message=f"leaked keys: {leaked}",
                evidence={"leaked_keys": leaked},
            ),
        )

    def approval_required_for(self, tool_name: str) -> None:
        """Assert the approval middleware intercepted ``tool_name``.

        Looks for an ``APPROVAL_REQUEST`` event matching the tool.

        Args:
            tool_name (`str`): The tool name.
        """
        found = any(
            e.get("type") == "APPROVAL_REQUEST"
            and e.get("tool_name") == tool_name
            for e in self.events
        )
        self._results.append(
            AssertionResult(
                name=f"approval_required_for:{tool_name}",
                passed=found,
                message=f"no approval request for {tool_name}",
            ),
        )

    def approval_was_cached(self, tool_name: str) -> None:
        """Assert the approval state cache has ``tool_name`` (ONCE strategy).

        Args:
            tool_name (`str`): The tool name.
        """
        cached = self._runner.approval_state_snapshot(
            self._session_id,
        )
        ok = tool_name in cached
        self._results.append(
            AssertionResult(
                name=f"approval_cached:{tool_name}",
                passed=ok,
                message=f"{tool_name} not in approval cache: {cached}",
            ),
        )

    # ── internal ────────────────────────────────────────────────

    @property
    def results(self) -> list[AssertionResult]:
        """Return the list of recorded assertion results."""
        return self._results
