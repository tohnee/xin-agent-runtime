# -*- coding: utf-8 -*-
"""TDD unit tests for ``ApprovalMiddleware`` (HITL approval gating).

Covers the four strategies required by P1-A:

* ``always``  — every tool call must be approved by a human
* ``once``    — first call of a tool in a session blocks; subsequent
                calls of the same tool are auto-approved
* ``never``   — passthrough (no approval required)
* ``predicate`` — conditional approval based on tool name / input

Also covers:

* Timeout handling when the approver does not respond in time
* Explicit rejection by the approver (``PermissionError`` raised)
* Tool allowlist / denylist overrides (``always_require_tools`` /
  ``never_require_tools``)
* Cross-turn state sharing for ``once`` via ``ApprovalStateCache``
* Wire-up: ``ApprovalConfig`` lives on ``XRuntimeConfig`` and the
  middleware is injected by ``create_xruntime_extension`` when enabled

The tests intentionally avoid a real agent / tool runtime: they use
``unittest.mock`` to fabricate ``agent``, ``tool_call``, and
``next_handler`` so the middleware can be exercised in isolation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable
from unittest.mock import MagicMock

import pytest

# Imports below will fail until the ApprovalMiddleware module exists
# (Red phase of TDD). They will start passing once the implementation
# lands in src/xruntime/_runtime/_middleware/_approval.py.
from xruntime._runtime._middleware._approval import (  # noqa: E402
    ApprovalConfig,
    ApprovalDecision,
    ApprovalMiddleware,
    ApprovalStateCache,
    ApprovalStrategy,
    ApprovalTimeoutError,
)


# ── helpers ──────────────────────────────────────────────────────────


class _MockToolCall:
    """Minimal stand-in for AS ``ToolCallBlock``."""

    def __init__(
        self,
        name: str,
        tool_input: Any | None = None,
    ) -> None:
        self.name = name
        self.tool_call_name = name
        self.input = tool_input if tool_input is not None else {}


class _MockAgentState:
    """Minimal stand-in for ``AgentState``."""

    def __init__(self, session_id: str = "sess-1") -> None:
        self.session_id = session_id


class _MockAgent:
    """Minimal stand-in for an AS ``Agent``."""

    def __init__(self, session_id: str = "sess-1") -> None:
        self.state = _MockAgentState(session_id=session_id)


def _make_next_handler(
    chunks: list[Any] | None = None,
) -> Callable[[], AsyncGenerator]:
    """Return an async generator ``next_handler`` yielding ``chunks``."""
    chunks = chunks if chunks is not None else [{"type": "tool_result"}]

    async def _gen() -> AsyncGenerator[Any, None]:
        for c in chunks:
            yield c

    return _gen


def _make_async_approver(
    decision: ApprovalDecision,
    delay: float = 0.0,
) -> Callable[..., Any]:
    """Return an async approver callback that resolves to ``decision``."""

    async def _approver(_request: Any) -> ApprovalDecision:
        if delay:
            await asyncio.sleep(delay)
        return decision

    return _approver


# ── 1. Strategy enum / config ────────────────────────────────────────


class TestApprovalStrategy:
    """Strategy enum and config defaults."""

    def test_strategy_values(self) -> None:
        """All four strategies must be present."""
        assert ApprovalStrategy.ALWAYS.value == "always"
        assert ApprovalStrategy.ONCE.value == "once"
        assert ApprovalStrategy.NEVER.value == "never"
        assert ApprovalStrategy.PREDICATE.value == "predicate"

    def test_config_defaults(self) -> None:
        """Default config should be disabled with ``never`` strategy."""
        cfg = ApprovalConfig()
        assert cfg.enabled is False
        assert cfg.strategy == ApprovalStrategy.NEVER
        assert cfg.timeout_seconds == 300.0
        assert cfg.always_require_tools == []
        assert cfg.never_require_tools == []

    def test_config_from_values(self) -> None:
        """Config should accept explicit values."""
        cfg = ApprovalConfig(
            enabled=True,
            strategy=ApprovalStrategy.ALWAYS,
            timeout_seconds=60.0,
            always_require_tools=["Bash", "Write"],
            never_require_tools=["Read"],
        )
        assert cfg.enabled is True
        assert cfg.strategy == ApprovalStrategy.ALWAYS
        assert cfg.timeout_seconds == 60.0
        assert "Bash" in cfg.always_require_tools
        assert "Write" in cfg.always_require_tools
        assert "Read" in cfg.never_require_tools


# ── 2. ApprovalDecision dataclass ────────────────────────────────────


class TestApprovalDecision:
    """Approval decision model."""

    def test_approved_decision(self) -> None:
        d = ApprovalDecision(approved=True)
        assert d.approved is True
        assert d.reason == ""
        assert d.approver == ""

    def test_rejected_decision(self) -> None:
        d = ApprovalDecision(
            approved=False,
            reason="risk: destructive command",
            approver="alice",
        )
        assert d.approved is False
        assert "destructive" in d.reason
        assert d.approver == "alice"


# ── 3. NEVER strategy (passthrough) ──────────────────────────────────


class TestNeverStrategy:
    """``never`` strategy must never block."""

    @pytest.mark.asyncio
    async def test_never_passes_through(self) -> None:
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.NEVER,
            approval_callback=None,
        )
        agent = _MockAgent()
        tool_call = _MockToolCall("Bash", {"command": "rm -rf /"})
        input_kwargs = {"tool_call": tool_call}

        chunks: list[Any] = []
        async for chunk in mw.on_acting(
            agent, input_kwargs, _make_next_handler()
        ):
            chunks.append(chunk)

        assert chunks == [{"type": "tool_result"}]


# ── 4. ALWAYS strategy ───────────────────────────────────────────────


class TestAlwaysStrategy:
    """``always`` strategy requires approval for every tool call."""

    @pytest.mark.asyncio
    async def test_always_approved_executes(self) -> None:
        approver = _make_async_approver(
            ApprovalDecision(approved=True, approver="bob"),
        )
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ALWAYS,
            approval_callback=approver,
        )
        agent = _MockAgent()
        tool_call = _MockToolCall("Read", {"path": "/tmp/x"})
        input_kwargs = {"tool_call": tool_call}

        chunks = [
            c
            async for c in mw.on_acting(
                agent,
                input_kwargs,
                _make_next_handler(),
            )
        ]
        assert chunks == [{"type": "tool_result"}]

    @pytest.mark.asyncio
    async def test_always_rejected_raises_permission_error(self) -> None:
        approver = _make_async_approver(
            ApprovalDecision(
                approved=False,
                reason="destructive",
                approver="bob",
            ),
        )
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ALWAYS,
            approval_callback=approver,
        )
        agent = _MockAgent()
        tool_call = _MockToolCall("Bash", {"command": "rm -rf /"})
        input_kwargs = {"tool_call": tool_call}

        with pytest.raises(PermissionError) as exc_info:
            async for _ in mw.on_acting(
                agent,
                input_kwargs,
                _make_next_handler(),
            ):
                pass

        assert "Bash" in str(exc_info.value)
        assert "destructive" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_always_invokes_approver_with_request(self) -> None:
        captured: list[Any] = []

        async def _approver(req: Any) -> ApprovalDecision:
            captured.append(req)
            return ApprovalDecision(approved=True)

        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ALWAYS,
            approval_callback=_approver,
            tenant_id="acme",
            user_id="alice",
        )
        agent = _MockAgent(session_id="sess-42")
        tool_call = _MockToolCall("Write", {"path": "/etc/x"})
        input_kwargs = {"tool_call": tool_call}

        async for _ in mw.on_acting(agent, input_kwargs, _make_next_handler()):
            pass

        assert len(captured) == 1
        req = captured[0]
        assert req.tool_name == "Write"
        assert req.session_id == "sess-42"
        assert req.tenant_id == "acme"
        assert req.user_id == "alice"
        assert req.tool_input == {"path": "/etc/x"}

    @pytest.mark.asyncio
    async def test_always_without_callback_times_out(self) -> None:
        """``always`` strategy without a callback must raise on timeout."""
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ALWAYS,
            approval_callback=None,
            timeout_seconds=0.05,
        )
        agent = _MockAgent()
        tool_call = _MockToolCall("Read")
        input_kwargs = {"tool_call": tool_call}

        with pytest.raises(ApprovalTimeoutError):
            async for _ in mw.on_acting(
                agent,
                input_kwargs,
                _make_next_handler(),
            ):
                pass


# ── 5. ONCE strategy ─────────────────────────────────────────────────


class TestOnceStrategy:
    """``once`` strategy: first call blocks, subsequent calls pass through."""

    @pytest.mark.asyncio
    async def test_once_first_call_requires_approval(self) -> None:
        calls: list[Any] = []

        async def _approver(req: Any) -> ApprovalDecision:
            calls.append(req)
            return ApprovalDecision(approved=True)

        state_cache = ApprovalStateCache()
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ONCE,
            approval_callback=_approver,
            state_cache=state_cache,
        )
        agent = _MockAgent(session_id="sess-A")
        tool_call = _MockToolCall("Bash", {"command": "ls"})
        input_kwargs = {"tool_call": tool_call}

        async for _ in mw.on_acting(agent, input_kwargs, _make_next_handler()):
            pass

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_once_second_call_skips_approval(self) -> None:
        calls: list[Any] = []

        async def _approver(req: Any) -> ApprovalDecision:
            calls.append(req)
            return ApprovalDecision(approved=True)

        state_cache = ApprovalStateCache()
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ONCE,
            approval_callback=_approver,
            state_cache=state_cache,
        )
        agent = _MockAgent(session_id="sess-A")

        # First call — blocks for approval
        async for _ in mw.on_acting(
            agent,
            {"tool_call": _MockToolCall("Bash", {"command": "ls"})},
            _make_next_handler(),
        ):
            pass

        # Second call of the SAME tool in the SAME session — pass through
        async for _ in mw.on_acting(
            agent,
            {"tool_call": _MockToolCall("Bash", {"command": "pwd"})},
            _make_next_handler(),
        ):
            pass

        assert len(calls) == 1, "second call must not invoke approver"

    @pytest.mark.asyncio
    async def test_once_different_tools_each_require_approval(self) -> None:
        calls: list[Any] = []

        async def _approver(req: Any) -> ApprovalDecision:
            calls.append(req)
            return ApprovalDecision(approved=True)

        state_cache = ApprovalStateCache()
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ONCE,
            approval_callback=_approver,
            state_cache=state_cache,
        )
        agent = _MockAgent(session_id="sess-B")

        async for _ in mw.on_acting(
            agent,
            {"tool_call": _MockToolCall("Bash")},
            _make_next_handler(),
        ):
            pass

        async for _ in mw.on_acting(
            agent,
            {"tool_call": _MockToolCall("Write")},
            _make_next_handler(),
        ):
            pass

        assert len(calls) == 2, "different tools must each be approved once"

    @pytest.mark.asyncio
    async def test_once_state_isolation_per_session(self) -> None:
        """Approval state must NOT leak across sessions."""
        calls: list[Any] = []

        async def _approver(req: Any) -> ApprovalDecision:
            calls.append(req)
            return ApprovalDecision(approved=True)

        state_cache = ApprovalStateCache()
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ONCE,
            approval_callback=_approver,
            state_cache=state_cache,
        )

        # Session A — Bash
        async for _ in mw.on_acting(
            _MockAgent(session_id="A"),
            {"tool_call": _MockToolCall("Bash")},
            _make_next_handler(),
        ):
            pass

        # Session B — Bash — must also require approval
        async for _ in mw.on_acting(
            _MockAgent(session_id="B"),
            {"tool_call": _MockToolCall("Bash")},
            _make_next_handler(),
        ):
            pass

        assert (
            len(calls) == 2
        ), "different sessions must not share approval state"

    @pytest.mark.asyncio
    async def test_once_rejected_does_not_cache(self) -> None:
        """A rejected approval must NOT mark the tool as pre-approved."""
        call_count = 0

        async def _approver(req: Any) -> ApprovalDecision:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ApprovalDecision(approved=False, reason="no")
            return ApprovalDecision(approved=True)

        state_cache = ApprovalStateCache()
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ONCE,
            approval_callback=_approver,
            state_cache=state_cache,
        )
        agent = _MockAgent(session_id="sess-R")
        input_kwargs = {"tool_call": _MockToolCall("Bash")}

        # First call — rejected
        with pytest.raises(PermissionError):
            async for _ in mw.on_acting(
                agent, input_kwargs, _make_next_handler()
            ):
                pass

        # Second call — must require approval again (not cached)
        async for _ in mw.on_acting(agent, input_kwargs, _make_next_handler()):
            pass

        assert call_count == 2, "rejected approval must not be cached"


# ── 6. PREDICATE strategy ────────────────────────────────────────────


class TestPredicateStrategy:
    """``predicate`` strategy: conditional approval based on tool/input."""

    @pytest.mark.asyncio
    async def test_predicate_true_requires_approval(self) -> None:
        calls: list[Any] = []

        async def _approver(req: Any) -> ApprovalDecision:
            calls.append(req)
            return ApprovalDecision(approved=True)

        def _is_destructive(tool_name: str, tool_input: dict) -> bool:
            cmd = tool_input.get("command", "")
            return "rm" in cmd

        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.PREDICATE,
            approval_callback=_approver,
            predicate=_is_destructive,
        )
        agent = _MockAgent()
        tool_call = _MockToolCall("Bash", {"command": "rm -rf /"})
        input_kwargs = {"tool_call": tool_call}

        async for _ in mw.on_acting(agent, input_kwargs, _make_next_handler()):
            pass

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_predicate_false_skips_approval(self) -> None:
        calls: list[Any] = []

        async def _approver(req: Any) -> ApprovalDecision:
            calls.append(req)
            return ApprovalDecision(approved=True)

        def _is_destructive(tool_name: str, tool_input: dict) -> bool:
            cmd = tool_input.get("command", "")
            return "rm" in cmd

        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.PREDICATE,
            approval_callback=_approver,
            predicate=_is_destructive,
        )
        agent = _MockAgent()
        tool_call = _MockToolCall("Bash", {"command": "ls"})
        input_kwargs = {"tool_call": tool_call}

        async for _ in mw.on_acting(agent, input_kwargs, _make_next_handler()):
            pass

        assert len(calls) == 0, "predicate=False must skip approval"

    @pytest.mark.asyncio
    async def test_predicate_missing_raises_value_error(self) -> None:
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.PREDICATE,
            approval_callback=_make_async_approver(
                ApprovalDecision(approved=True)
            ),
            predicate=None,
        )
        agent = _MockAgent()
        with pytest.raises(ValueError, match="predicate"):
            async for _ in mw.on_acting(
                agent,
                {"tool_call": _MockToolCall("Bash")},
                _make_next_handler(),
            ):
                pass


# ── 7. Tool allowlist / denylist overrides ──────────────────────────


class TestToolOverrides:
    """``always_require_tools`` / ``never_require_tools`` overrides."""

    @pytest.mark.asyncio
    async def test_always_require_tools_forces_approval(self) -> None:
        """Even with ``never`` strategy, ``always_require_tools`` forces HITL."""
        calls: list[Any] = []

        async def _approver(req: Any) -> ApprovalDecision:
            calls.append(req)
            return ApprovalDecision(approved=True)

        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.NEVER,
            approval_callback=_approver,
            always_require_tools={"Bash"},
        )
        agent = _MockAgent()

        # Bash — must be approved
        async for _ in mw.on_acting(
            agent,
            {"tool_call": _MockToolCall("Bash")},
            _make_next_handler(),
        ):
            pass

        # Read — pass through
        async for _ in mw.on_acting(
            agent,
            {"tool_call": _MockToolCall("Read")},
            _make_next_handler(),
        ):
            pass

        assert len(calls) == 1, "only Bash should require approval"

    @pytest.mark.asyncio
    async def test_never_require_tools_skips_approval(self) -> None:
        """Even with ``always`` strategy, ``never_require_tools`` skips HITL."""
        calls: list[Any] = []

        async def _approver(req: Any) -> ApprovalDecision:
            calls.append(req)
            return ApprovalDecision(approved=True)

        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ALWAYS,
            approval_callback=_approver,
            never_require_tools={"Read"},
        )
        agent = _MockAgent()

        # Read — pass through (denylist override)
        async for _ in mw.on_acting(
            agent,
            {"tool_call": _MockToolCall("Read")},
            _make_next_handler(),
        ):
            pass

        # Bash — must be approved
        async for _ in mw.on_acting(
            agent,
            {"tool_call": _MockToolCall("Bash")},
            _make_next_handler(),
        ):
            pass

        assert len(calls) == 1, "Read should skip approval, Bash should not"


# ── 8. Timeout handling ──────────────────────────────────────────────


class TestApprovalTimeout:
    """Approval request timeout."""

    @pytest.mark.asyncio
    async def test_timeout_raises_approval_timeout_error(self) -> None:
        async def _slow_approver(_req: Any) -> ApprovalDecision:
            await asyncio.sleep(10)
            return ApprovalDecision(approved=True)

        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ALWAYS,
            approval_callback=_slow_approver,
            timeout_seconds=0.05,
        )
        agent = _MockAgent()

        with pytest.raises(ApprovalTimeoutError):
            async for _ in mw.on_acting(
                agent,
                {"tool_call": _MockToolCall("Bash")},
                _make_next_handler(),
            ):
                pass

    @pytest.mark.asyncio
    async def test_timeout_default_300_seconds(self) -> None:
        cfg = ApprovalConfig()
        assert cfg.timeout_seconds == 300.0


# ── 9. ApprovalStateCache (cross-turn sharing) ──────────────────────


class TestApprovalStateCache:
    """The state cache used by ``once`` strategy."""

    @pytest.mark.asyncio
    async def test_mark_approved_records_tool(self) -> None:
        cache = ApprovalStateCache()
        await cache.mark_approved("sess-1", "Bash")
        assert await cache.is_approved("sess-1", "Bash") is True

    @pytest.mark.asyncio
    async def test_is_approved_returns_false_for_unknown(self) -> None:
        cache = ApprovalStateCache()
        assert await cache.is_approved("sess-1", "Bash") is False
        assert await cache.is_approved("sess-1", "Read") is False

    @pytest.mark.asyncio
    async def test_isolation_per_session(self) -> None:
        cache = ApprovalStateCache()
        await cache.mark_approved("A", "Bash")
        assert await cache.is_approved("A", "Bash") is True
        assert await cache.is_approved("B", "Bash") is False

    @pytest.mark.asyncio
    async def test_clear_session(self) -> None:
        cache = ApprovalStateCache()
        await cache.mark_approved("sess-1", "Bash")
        await cache.mark_approved("sess-1", "Write")
        await cache.clear_session("sess-1")
        assert await cache.is_approved("sess-1", "Bash") is False
        assert await cache.is_approved("sess-1", "Write") is False


# ── 10. Missing tool_call (passthrough) ──────────────────────────────


class TestMissingToolCall:
    """When ``tool_call`` is absent, the middleware must pass through."""

    @pytest.mark.asyncio
    async def test_no_tool_call_passes_through(self) -> None:
        mw = ApprovalMiddleware(
            strategy=ApprovalStrategy.ALWAYS,
            approval_callback=_make_async_approver(
                ApprovalDecision(approved=True)
            ),
        )
        agent = _MockAgent()

        # No tool_call key — should pass through without approval
        chunks = [
            c
            async for c in mw.on_acting(
                agent,
                {},
                _make_next_handler(),
            )
        ]
        assert chunks == [{"type": "tool_result"}]


# ── 11. Config wiring ────────────────────────────────────────────────


class TestApprovalConfigWiring:
    """``ApprovalConfig`` should be exposed on ``XRuntimeConfig``."""

    def test_xruntime_config_has_approval(self) -> None:
        from xruntime._config import XRuntimeConfig

        cfg = XRuntimeConfig()
        assert hasattr(cfg, "approval")
        assert isinstance(cfg.approval, ApprovalConfig)
        assert cfg.approval.enabled is False

    def test_xruntime_config_approval_from_dict(self) -> None:
        from xruntime._config import XRuntimeConfig

        cfg = XRuntimeConfig(
            approval={
                "enabled": True,
                "strategy": "always",
                "timeout_seconds": 60.0,
                "always_require_tools": ["Bash"],
            },
        )
        assert cfg.approval.enabled is True
        assert cfg.approval.strategy == ApprovalStrategy.ALWAYS
        assert cfg.approval.timeout_seconds == 60.0
        assert "Bash" in cfg.approval.always_require_tools
