# -*- coding: utf-8 -*-
"""Tests for migration framework — Migrator CLI + MigrationShimMiddleware."""
from unittest.mock import MagicMock

from xruntime._runtime._migrator import (
    Migrator,
    MigrationShimMiddleware,
    SessionVersionChecker,
    MigrationResult,
    SCHEMA_VERSION,
)


class TestSessionVersionChecker:
    """Tests for session schema version detection."""

    def test_current_version(self) -> None:
        """Current schema version should be defined."""
        assert SCHEMA_VERSION == 1

    def test_is_current(self) -> None:
        """Session with current version should pass."""
        checker = SessionVersionChecker()
        assert checker.is_current({"version": 1})

    def test_is_old(self) -> None:
        """Session with old version should not pass."""
        checker = SessionVersionChecker()
        assert not checker.is_current({"version": 0})

    def test_no_version_field(self) -> None:
        """Session without version field should be old."""
        checker = SessionVersionChecker()
        assert not checker.is_current({})

    def test_unknown_future_version(self) -> None:
        """Future version should be treated as current (forward compat)."""
        checker = SessionVersionChecker()
        assert checker.is_current({"version": 2})


class TestMigrationResult:
    """Tests for MigrationResult."""

    def test_success_result(self) -> None:
        """Successful migration should record count."""
        result = MigrationResult(
            migrated=5,
            skipped=2,
            failed=0,
            errors=[],
        )
        assert result.migrated == 5
        assert result.failed == 0
        assert result.is_success

    def test_failure_result(self) -> None:
        """Failed migration should record errors."""
        result = MigrationResult(
            migrated=3,
            skipped=0,
            failed=1,
            errors=["session-xyz: schema mismatch"],
        )
        assert not result.is_success
        assert len(result.errors) == 1


class TestMigrator:
    """Tests for Migrator CLI skeleton."""

    def test_creation(self) -> None:
        """Migrator should be creatable."""
        m = Migrator()
        assert m is not None

    async def test_dry_run_no_data(self) -> None:
        """Dry run with no sessions should return empty result."""
        m = Migrator()
        result = await m.dry_run([])
        assert result.migrated == 0
        assert result.is_success

    async def test_dry_run_current_sessions_skipped(self) -> None:
        """Sessions with current version should be skipped."""
        m = Migrator()
        sessions = [
            {"id": "s1", "version": 1, "data": {}},
            {"id": "s2", "version": 1, "data": {}},
        ]
        result = await m.dry_run(sessions)
        assert result.migrated == 0
        assert result.skipped == 2

    async def test_dry_run_old_sessions_flagged(self) -> None:
        """Old sessions should be flagged as needing migration."""
        m = Migrator()
        sessions = [
            {"id": "s1", "version": 0, "data": {"old_field": "val"}},
        ]
        result = await m.dry_run(sessions)
        assert result.migrated == 0
        assert result.skipped == 0
        assert result.failed == 1

    async def test_execute_current_sessions_noop(self) -> None:
        """Executing on current sessions should be a no-op."""
        m = Migrator()
        sessions = [
            {"id": "s1", "version": 1, "data": {}},
        ]
        result = await m.execute(sessions)
        assert result.migrated == 0
        assert result.skipped == 1
        assert result.is_success


class TestMigrationShimMiddleware:
    """Tests for MigrationShimMiddleware."""

    def test_creation(self) -> None:
        """Middleware should be creatable."""
        mw = MigrationShimMiddleware()
        assert mw is not None

    def test_inherits_middleware_base(self) -> None:
        """MigrationShimMiddleware should inherit MiddlewareBase (Issue 12)."""
        from agentscope.middleware import MiddlewareBase

        assert issubclass(MigrationShimMiddleware, MiddlewareBase)

    def test_is_implemented_on_acting(self) -> None:
        """Should report on_acting as implemented."""
        mw = MigrationShimMiddleware()
        assert mw.is_implemented("on_acting") is True

    def test_check_session_current(self) -> None:
        """Current session should pass check."""
        mw = MigrationShimMiddleware()
        assert mw.check_session({"version": 1})

    def test_check_session_old_warns(self) -> None:
        """Old session should not pass check."""
        mw = MigrationShimMiddleware()
        assert not mw.check_session({"version": 0})

    def test_check_session_no_version_warns(self) -> None:
        """Session without version should not pass."""
        mw = MigrationShimMiddleware()
        assert not mw.check_session({"name": "agent"})

    async def test_on_acting_passes_through(self) -> None:
        """on_acting should pass through to next handler."""
        mw = MigrationShimMiddleware()

        agent = MagicMock()
        agent.state = MagicMock()
        agent.state.session_id = "s1"
        agent.state.version = 1

        yielded = MagicMock()

        async def mock_next():
            yield yielded

        gen = mw.on_acting(agent, {"tool_call": None}, mock_next)
        results = []
        async for chunk in gen:
            results.append(chunk)
        assert results == [yielded]


class TestMigratorPublicExport:
    """The migration framework is exported from the package (issue #16)."""

    def test_migrator_exported(self) -> None:
        """Migrator is reachable from the top-level package."""
        import xruntime

        assert hasattr(xruntime, "Migrator")
        assert hasattr(xruntime, "MigrationShimMiddleware")
        assert hasattr(xruntime, "MigrationResult")
        assert hasattr(xruntime, "SCHEMA_VERSION")

    def test_exported_migrator_is_the_class(self) -> None:
        """The exported Migrator is the implementation class."""
        import xruntime
        from xruntime._runtime._migrator import Migrator

        assert xruntime.Migrator is Migrator
