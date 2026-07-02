# -*- coding: utf-8 -*-
"""TDD tests for per-session lock isolation in ``ApprovalStateCache``.

The cache must use a separate :class:`asyncio.Lock` per session id so
concurrent tool calls on *different* sessions do not serialize
against each other.  The previous implementation used a single
global lock shared by all sessions, which violated the docstring's
"concurrent tool calls within the same session do not race" promise
by also serializing calls *across* sessions.

These tests drive the contract from the outside: holding session A's
lock via ``_get_lock`` must not block session B, but must block
session A itself.
"""
from __future__ import annotations

import asyncio

import pytest

from xruntime._runtime._middleware._approval import (  # noqa: E402
    ApprovalStateCache,
)


# ── 1. Per-session lock isolation ───────────────────────────────


class TestPerSessionLockIsolation:
    """``_get_lock`` returns a distinct lock per session id."""

    @pytest.mark.asyncio
    async def test_get_lock_returns_distinct_locks_per_session(
        self,
    ) -> None:
        cache = ApprovalStateCache()
        lock_a = cache._get_lock("session-a")
        lock_b = cache._get_lock("session-b")
        assert lock_a is not lock_b

    @pytest.mark.asyncio
    async def test_get_lock_returns_same_lock_for_same_session(
        self,
    ) -> None:
        cache = ApprovalStateCache()
        lock_a1 = cache._get_lock("session-a")
        lock_a2 = cache._get_lock("session-a")
        assert lock_a1 is lock_a2

    @pytest.mark.asyncio
    async def test_other_session_not_blocked_by_held_lock(
        self,
    ) -> None:
        """Session B's ``is_approved`` must NOT block on session A's
        lock — this is the core per-session isolation guarantee."""
        cache = ApprovalStateCache()
        lock_a = cache._get_lock("session-a")
        await lock_a.acquire()
        try:
            result = await asyncio.wait_for(
                cache.is_approved("session-b", "tool-x"),
                timeout=0.5,
            )
            assert result is False
        finally:
            lock_a.release()

    @pytest.mark.asyncio
    async def test_same_session_blocks_on_held_lock(self) -> None:
        """Session A's ``is_approved`` MUST block when its own lock
        is held — proves ``is_approved`` actually acquires the
        per-session lock rather than using a global lock."""
        cache = ApprovalStateCache()
        lock_a = cache._get_lock("session-a")
        await lock_a.acquire()
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    cache.is_approved("session-a", "tool-x"),
                    timeout=0.1,
                )
        finally:
            lock_a.release()

    @pytest.mark.asyncio
    async def test_mark_approved_populates_per_session_lock(
        self,
    ) -> None:
        """``mark_approved`` must create the per-session lock
        entry in ``_locks`` (proves it routes through
        ``_get_lock``)."""
        cache = ApprovalStateCache()
        await cache.mark_approved("session-x", "Bash")
        assert "session-x" in cache._locks


# ── 2. clear_session cleans up the lock ─────────────────────────


class TestClearSessionLockCleanup:
    """``clear_session`` must drop the per-session lock so the
    ``_locks`` dict does not grow unboundedly across session
    lifetimes."""

    @pytest.mark.asyncio
    async def test_clear_session_removes_lock(self) -> None:
        cache = ApprovalStateCache()
        cache._get_lock("sess-1")
        assert "sess-1" in cache._locks
        await cache.clear_session("sess-1")
        assert "sess-1" not in cache._locks

    @pytest.mark.asyncio
    async def test_clear_session_only_removes_target_lock(
        self,
    ) -> None:
        cache = ApprovalStateCache()
        cache._get_lock("sess-A")
        cache._get_lock("sess-B")
        await cache.clear_session("sess-A")
        assert "sess-A" not in cache._locks
        assert "sess-B" in cache._locks

    @pytest.mark.asyncio
    async def test_clear_session_after_mark_approved_removes_lock(
        self,
    ) -> None:
        """End-to-end: mark_approved creates the lock, clear_session
        must remove it."""
        cache = ApprovalStateCache()
        await cache.mark_approved("sess-1", "Bash")
        assert "sess-1" in cache._locks
        await cache.clear_session("sess-1")
        assert "sess-1" not in cache._locks


# ── 3. Basic functionality (no regression) ──────────────────────


class TestBasicFunctionalityNoRegression:
    """``mark_approved`` → ``is_approved`` → ``clear_session`` still
    works correctly under per-session locking."""

    @pytest.mark.asyncio
    async def test_mark_then_is_approved(self) -> None:
        cache = ApprovalStateCache()
        await cache.mark_approved("sess-1", "Bash")
        assert await cache.is_approved("sess-1", "Bash") is True
        assert await cache.is_approved("sess-1", "Read") is False

    @pytest.mark.asyncio
    async def test_is_approved_returns_false_for_unknown(
        self,
    ) -> None:
        cache = ApprovalStateCache()
        assert await cache.is_approved("sess-1", "Bash") is False

    @pytest.mark.asyncio
    async def test_clear_session_drops_state(self) -> None:
        cache = ApprovalStateCache()
        await cache.mark_approved("sess-1", "Bash")
        await cache.mark_approved("sess-1", "Write")
        await cache.clear_session("sess-1")
        assert await cache.is_approved("sess-1", "Bash") is False
        assert await cache.is_approved("sess-1", "Write") is False

    @pytest.mark.asyncio
    async def test_state_isolation_per_session(self) -> None:
        cache = ApprovalStateCache()
        await cache.mark_approved("A", "Bash")
        assert await cache.is_approved("A", "Bash") is True
        assert await cache.is_approved("B", "Bash") is False
