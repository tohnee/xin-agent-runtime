# -*- coding: utf-8 -*-
"""RBAC middleware — role-based tool access control.

Defines roles as collections of :class:`RbacRule` (tool pattern +
allow/deny).  Sessions are assigned roles; the middleware checks
tool calls against the assigned role before execution.

Inherits :class:`agentscope.middleware.MiddlewareBase` and implements
``on_acting`` to enforce role-based deny before tool execution.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase

logger = logging.getLogger(__name__)


@dataclass
class RbacRule:
    """A single RBAC rule.

    Args:
        tool_pattern (`str`):
            Tool name pattern (supports glob).
        action (`str`):
            ``"allow"`` or ``"deny"``.
    """

    tool_pattern: str
    action: str

    def matches(self, tool_name: str) -> bool:
        """Check if a tool name matches this rule's pattern.

        Args:
            tool_name (`str`):
                The tool name to check.

        Returns:
            `bool`: ``True`` if the pattern matches.
        """
        return fnmatch.fnmatch(tool_name, self.tool_pattern)


@dataclass
class RoleDefinition:
    """A role consisting of a set of :class:`RbacRule` instances.

    Args:
        name (`str`):
            Role name.
        rules (`list[RbacRule]`):
            The rules for this role, evaluated in order.
    """

    name: str
    rules: list[RbacRule] = field(default_factory=list)

    def check_tool(self, tool_name: str) -> str:
        """Check if a tool is allowed by this role.

        Args:
            tool_name (`str`):
                The tool name to check.

        Returns:
            `str`: ``"allow"`` or ``"deny"``.
        """
        for rule in self.rules:
            if rule.matches(tool_name):
                return rule.action
        return "deny"


class RbacMiddleware(MiddlewareBase):
    """Middleware that enforces role-based tool access.

    Args:
        roles (`dict[str, RoleDefinition]`):
            All defined roles, keyed by name.
    """

    def __init__(
        self,
        roles: dict[str, RoleDefinition],
        default_role: str | None = None,
    ) -> None:
        """Initialize the middleware."""
        self.roles = roles
        self.default_role = default_role
        self._session_roles: dict[str, str] = {}

    def assign_role(
        self,
        session_id: str,
        role_name: str,
    ) -> None:
        """Assign a role to a session.

        Args:
            session_id (`str`):
                The session id.
            role_name (`str`):
                The role name.
        """
        self._session_roles[session_id] = role_name

    def get_role(self, session_id: str) -> str | None:
        """Get the role assigned to a session.

        Args:
            session_id (`str`):
                The session id.

        Returns:
            `str | None`: The role name, or ``None``.
        """
        return self._session_roles.get(session_id)

    def check_tool(
        self,
        session_id: str,
        tool_name: str,
    ) -> str:
        """Check if a tool is allowed for a session's role.

        Args:
            session_id (`str`):
                The session id.
            tool_name (`str`):
                The tool name to check.

        Returns:
            `str`: ``"allow"`` or ``"deny"``.
        """
        role_name = self._session_roles.get(session_id)
        if role_name is None:
            role_name = self.default_role
        if role_name is None:
            return "deny"
        role = self.roles.get(role_name)
        if role is None:
            return "deny"
        return role.check_tool(tool_name)

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Enforce RBAC before tool execution.

        Args:
            agent (`Agent`):
                The agent instance.
            input_kwargs (`dict`):
                Contains ``tool_call``.
            next_handler (`Callable`):
                The next middleware or ``_acting_impl``.

        Yields:
            Tool chunks from the tool execution.

        Raises:
            PermissionError: If the tool is denied by RBAC.
        """
        tool_call = input_kwargs.get("tool_call")
        tool_name = ""
        if tool_call is not None:
            tool_name = getattr(
                tool_call,
                "tool_call_name",
                "",
            ) or getattr(tool_call, "name", "")

        session_id = ""
        if hasattr(agent, "state") and hasattr(
            agent.state,
            "session_id",
        ):
            session_id = agent.state.session_id or ""

        role_name = self._session_roles.get(session_id, self.default_role)

        logger.info(
            f"[RBAC-CHECK] session={session_id}, role={role_name}, "
            f"tool={tool_name}",
        )

        decision = self.check_tool(session_id, tool_name)

        if decision == "deny":
            logger.warning(
                f"[RBAC-DENIED] session={session_id}, role={role_name}, "
                f"tool={tool_name} — Access DENIED",
            )
            raise PermissionError(
                f"RBAC denied tool '{tool_name}' "
                f"for session '{session_id}'",
            )

        logger.info(
            f"[RBAC-ALLOWED] session={session_id}, role={role_name}, "
            f"tool={tool_name} — Access ALLOWED",
        )

        async for chunk in next_handler():
            yield chunk
