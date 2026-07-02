# -*- coding: utf-8 -*-
"""Migration framework — Migrator CLI + MigrationShimMiddleware.

v1 strategy: framework skeleton only.  Old xruntime schema is
unavailable, so no automatic data conversion is performed.  The
framework detects old-version sessions and flags them; manual
migration is required (see docs).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase


SCHEMA_VERSION: int = 1
"""Current XRuntime session schema version."""

_logger = logging.getLogger(__name__)


class SessionVersionChecker:
    """Checks whether a session record matches the current schema.

    Sessions with a ``version`` field >= :data:`SCHEMA_VERSION`
    are considered current.  Sessions without a ``version`` field
    or with a lower version are considered old.
    """

    def is_current(self, session: dict[str, Any]) -> bool:
        """Check if a session is current.

        Args:
            session (`dict`):
                The session record dict.

        Returns:
            `bool`: ``True`` if the session is current.
        """
        version = session.get("version", 0)
        return version >= SCHEMA_VERSION


@dataclass
class MigrationResult:
    """Result of a migration run.

    Args:
        migrated (`int`):
            Number of sessions successfully migrated.
        skipped (`int`):
            Number of sessions skipped (already current).
        failed (`int`):
            Number of sessions that failed migration.
        errors (`list[str]`):
            Error messages for failed sessions.
    """

    migrated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        """Whether the migration succeeded (no failures).

        Returns:
            `bool`: ``True`` if ``failed == 0``.
        """
        return self.failed == 0


class Migrator:
    """CLI skeleton for migrating sessions between schema versions.

    v1 only validates structure — no data conversion is performed.
    """

    def __init__(self) -> None:
        """Initialize the migrator."""
        self.checker = SessionVersionChecker()

    async def dry_run(
        self,
        sessions: list[dict[str, Any]],
    ) -> MigrationResult:
        """Validate sessions without modifying them.

        Args:
            sessions (`list[dict]`):
                Session records to validate.

        Returns:
            `MigrationResult`: Validation result.
        """
        result = MigrationResult()
        for session in sessions:
            if self.checker.is_current(session):
                result.skipped += 1
            else:
                result.failed += 1
                result.errors.append(
                    f"{session.get('id', 'unknown')}: "
                    f"old schema version "
                    f"({session.get('version', 'missing')})",
                )
        return result

    async def execute(
        self,
        sessions: list[dict[str, Any]],
    ) -> MigrationResult:
        """Execute migration on sessions.

        v1: current sessions are skipped; old sessions are flagged
        as failed (no automatic conversion available).

        Args:
            sessions (`list[dict]`):
                Session records to migrate.

        Returns:
            `MigrationResult`: Migration result.
        """
        result = MigrationResult()
        for session in sessions:
            if self.checker.is_current(session):
                result.skipped += 1
            else:
                result.failed += 1
                result.errors.append(
                    f"{session.get('id', 'unknown')}: "
                    f"manual migration required "
                    f"(old version {session.get('version', 'missing')})",
                )
        return result


class MigrationShimMiddleware(MiddlewareBase):
    """Middleware that flags old-version sessions at load time.

    When a session is loaded, this middleware checks its schema
    version.  Old sessions are flagged with a warning; current
    sessions pass through.  Inherits :class:`MiddlewareBase` so it
    can be used in the AS agent middleware chain.
    """

    def __init__(self) -> None:
        """Initialize the middleware."""
        self.checker = SessionVersionChecker()

    def check_session(self, session: dict[str, Any]) -> bool:
        """Check if a session is current.

        Args:
            session (`dict`):
                The session record.

        Returns:
            `bool`: ``True`` if current, ``False`` if old.
        """
        return self.checker.is_current(session)

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Pass through to next handler, warning on old sessions.

        Args:
            agent (`Agent`):
                The agent instance.
            input_kwargs (`dict`):
                Contains ``tool_call``.
            next_handler (`Callable`):
                The next middleware or ``_acting_impl``.

        Yields:
            Tool chunks from the tool execution.
        """
        if hasattr(agent, "state") and hasattr(
            agent.state,
            "session_id",
        ):
            session_id = agent.state.session_id or ""
            if session_id:
                session_info = {"version": 0}
                if hasattr(agent.state, "version"):
                    session_info["version"] = agent.state.version
                if not self.checker.is_current(session_info):
                    _logger.warning(
                        "Session %s has old schema version; "
                        "manual migration may be needed",
                        session_id,
                    )
        async for chunk in next_handler():
            yield chunk
