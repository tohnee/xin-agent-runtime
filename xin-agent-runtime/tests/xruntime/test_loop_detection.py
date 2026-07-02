# -*- coding: utf-8 -*-
"""Tests for LoopDetectionMiddleware."""
from __future__ import annotations

import pytest

from xruntime._runtime._middleware._loop_detection import (
    LoopDetectionConfig,
    LoopDetectionMiddleware,
)


class FakeToolCall:
    """Minimal tool-call stub."""

    def __init__(
        self,
        name: str = "bash",
        input: dict | None = None,
    ) -> None:
        self.name = name
        self.input = input or {}


class FakeAgent:
    """Minimal agent stub."""

    def __init__(self) -> None:
        self.name = "test-agent"


async def _empty_gen():
    """Empty async generator simulating next_handler."""
    return
    yield  # pylint: disable=unreachable


def _make_handler():
    """Create a no-op next_handler."""
    return lambda: _empty_gen()


class TestLoopDetectionConfig:
    """Config defaults and customisation."""

    def test_defaults(self) -> None:
        cfg = LoopDetectionConfig()
        assert cfg.max_repeats == 3
        assert cfg.window_size == 10
        assert "repeating" in cfg.block_message.lower()

    def test_custom(self) -> None:
        cfg = LoopDetectionConfig(
            max_repeats=5,
            window_size=20,
            block_message="Stop!",
        )
        assert cfg.max_repeats == 5
        assert cfg.window_size == 20
        assert cfg.block_message == "Stop!"


class TestLoopDetectionMiddleware:
    """Middleware behaviour."""

    @pytest.fixture
    def mw(self) -> LoopDetectionMiddleware:
        return LoopDetectionMiddleware(
            LoopDetectionConfig(max_repeats=3, window_size=10),
        )

    @pytest.fixture
    def agent(self) -> FakeAgent:
        return FakeAgent()

    @pytest.mark.asyncio
    async def test_no_repeat_proceeds(
        self,
        mw: LoopDetectionMiddleware,
        agent: FakeAgent,
    ) -> None:
        """Different tool calls don't trigger."""
        for name in ["bash", "read", "write"]:
            tc = FakeToolCall(name=name, input={"path": f"/{name}"})
            events = []
            async for evt in mw.on_acting(
                agent,
                {"tool_call": tc},
                _make_handler(),
            ):
                events.append(evt)
            # Should not yield any system message (no loop)
            assert len(events) == 0

    @pytest.mark.asyncio
    async def test_same_tool_different_args_ok(
        self,
        mw: LoopDetectionMiddleware,
        agent: FakeAgent,
    ) -> None:
        """Same tool, different args → no block."""
        for i in range(5):
            tc = FakeToolCall(
                name="bash",
                input={"command": f"echo {i}"},
            )
            events = []
            async for evt in mw.on_acting(
                agent,
                {"tool_call": tc},
                _make_handler(),
            ):
                events.append(evt)
            assert len(events) == 0

    @pytest.mark.asyncio
    async def test_repeat_within_limit_ok(
        self,
        mw: LoopDetectionMiddleware,
        agent: FakeAgent,
    ) -> None:
        """Repeat count ≤ max_repeats → no block."""
        tc = FakeToolCall(name="bash", input={"command": "ls"})
        for _ in range(3):  # max_repeats=3
            events = []
            async for evt in mw.on_acting(
                agent,
                {"tool_call": tc},
                _make_handler(),
            ):
                events.append(evt)
            assert len(events) == 0

    @pytest.mark.asyncio
    async def test_repeat_exceeds_limit_blocked(
        self,
        mw: LoopDetectionMiddleware,
        agent: FakeAgent,
    ) -> None:
        """Repeat count > max_repeats → block message yielded."""
        tc = FakeToolCall(name="bash", input={"command": "ls"})
        # Fill up to max_repeats (3 calls, no block)
        for _ in range(3):
            async for _ in mw.on_acting(
                agent,
                {"tool_call": tc},
                _make_handler(),
            ):
                pass

        # 4th call → should trigger block
        events = []
        async for evt in mw.on_acting(
            agent,
            {"tool_call": tc},
            _make_handler(),
        ):
            events.append(evt)
        assert len(events) == 1
        assert "repeating" in str(events[0].content).lower()

    @pytest.mark.asyncio
    async def test_window_size_resets(
        self,
        agent: FakeAgent,
    ) -> None:
        """Old entries outside window don't count."""
        mw = LoopDetectionMiddleware(
            LoopDetectionConfig(
                max_repeats=2,
                window_size=3,
            ),
        )
        tc = FakeToolCall(name="bash", input={"command": "ls"})

        # 2 calls (within limit)
        for _ in range(2):
            async for _ in mw.on_acting(
                agent,
                {"tool_call": tc},
                _make_handler(),
            ):
                pass

        # Push 2 different calls to evict old entries
        for i in range(2):
            other = FakeToolCall(
                name="read",
                input={"path": f"/file{i}"},
            )
            async for _ in mw.on_acting(
                agent,
                {"tool_call": other},
                _make_handler(),
            ):
                pass

        # Now window has [read, read], so the next bash call
        # is the only bash in window → no block
        events = []
        async for evt in mw.on_acting(
            agent,
            {"tool_call": tc},
            _make_handler(),
        ):
            events.append(evt)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_reset_clears_history(
        self,
        mw: LoopDetectionMiddleware,
        agent: FakeAgent,
    ) -> None:
        """reset() clears all history."""
        tc = FakeToolCall(name="bash", input={"command": "ls"})
        for _ in range(3):
            async for _ in mw.on_acting(
                agent,
                {"tool_call": tc},
                _make_handler(),
            ):
                pass
        assert len(mw.history) == 3

        mw.reset()
        assert len(mw.history) == 0

        # After reset, same call is fresh
        events = []
        async for evt in mw.on_acting(
            agent,
            {"tool_call": tc},
            _make_handler(),
        ):
            events.append(evt)
        assert len(events) == 0

    def test_history_is_readonly(
        self,
        mw: LoopDetectionMiddleware,
    ) -> None:
        """history property returns a copy."""
        h = mw.history
        h.append(("hack", "123"))
        # Original should be unchanged
        assert len(mw.history) == 0

    @pytest.mark.asyncio
    async def test_missing_tool_call_key(
        self,
        mw: LoopDetectionMiddleware,
        agent: FakeAgent,
    ) -> None:
        """Missing tool_call in input_kwargs doesn't crash."""
        events = []
        async for evt in mw.on_acting(
            agent,
            {},
            _make_handler(),
        ):
            events.append(evt)
        # Should not crash, just record as ("unknown", hash of {})
        assert len(events) == 0
        assert mw.history[0][0] == "unknown"

    @pytest.mark.asyncio
    async def test_block_message_content(
        self,
        agent: FakeAgent,
    ) -> None:
        """Custom block message is used."""
        custom_msg = "STOP LOOPING!"
        mw = LoopDetectionMiddleware(
            LoopDetectionConfig(
                max_repeats=1,
                window_size=5,
                block_message=custom_msg,
            ),
        )
        tc = FakeToolCall(name="bash", input={"command": "ls"})

        # First call: count=1, max_repeats=1 → OK
        async for _ in mw.on_acting(
            agent,
            {"tool_call": tc},
            _make_handler(),
        ):
            pass

        # Second call: count=2 > max_repeats=1 → BLOCK
        events = []
        async for evt in mw.on_acting(
            agent,
            {"tool_call": tc},
            _make_handler(),
        ):
            events.append(evt)
        assert len(events) == 1
        assert custom_msg in str(events[0].content)
