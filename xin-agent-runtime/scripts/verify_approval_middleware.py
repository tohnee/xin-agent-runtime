# -*- coding: utf-8 -*-
"""Local verification script for ApprovalMiddleware exception handling.

Runs five scenarios against the real ApprovalMiddleware (no real agent
or tool runtime required — uses minimal mocks):

1. APPROVED  — approver returns approved=True → tool executes
2. DENIED    — approver returns approved=False → PermissionError
3. TIMEOUT   — approver hangs → ApprovalTimeoutError
4. ONCE      — first call blocks, second call auto-passthrough
5. PREDICATE — destructive command blocked, safe command passthrough

Exit code 0 = all scenarios behaved as expected.
Run:  python scripts/verify_approval_middleware.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, AsyncGenerator, Callable

# Configure logging so middleware INFO/WARNING messages are visible.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("approval-verify")


# ── minimal mocks (no agent / tool runtime needed) ──────────────────


class _ToolCall:
    """Stand-in for AS ``ToolCallBlock``."""

    def __init__(self, name: str, tool_input: Any | None = None) -> None:
        self.name = name
        self.tool_call_name = name
        self.input = tool_input if tool_input is not None else {}


class _AgentState:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _Agent:
    def __init__(self, session_id: str = "verify-sess") -> None:
        self.state = _AgentState(session_id)


def _next_handler(chunks: list[Any] | None = None) -> Callable[[], AsyncGenerator]:
    """Return an async generator that yields ``chunks``."""
    chunks = chunks if chunks is not None else [{"type": "tool_result", "ok": True}]

    async def _gen() -> AsyncGenerator[Any, None]:
        for c in chunks:
            yield c

    return _gen


# ── scenarios ────────────────────────────────────────────────────────


async def scenario_approved() -> bool:
    """Scenario 1: approver approves → tool executes."""
    from xruntime._runtime._middleware._approval import (
        ApprovalDecision,
        ApprovalMiddleware,
        ApprovalStrategy,
    )

    async def approver(_req: Any) -> ApprovalDecision:
        return ApprovalDecision(approved=True, approver="alice")

    mw = ApprovalMiddleware(
        strategy=ApprovalStrategy.ALWAYS,
        approval_callback=approver,
        timeout_seconds=2.0,
    )
    chunks = [
        c
        async for c in mw.on_acting(
            _Agent(),
            {"tool_call": _ToolCall("Read", {"path": "/etc/hosts"})},
            _next_handler(),
        )
    ]
    ok = chunks == [{"type": "tool_result", "ok": True}]
    log.info("[1/5 APPROVED]   %s — %d chunks yielded", "PASS" if ok else "FAIL", len(chunks))
    return ok


async def scenario_denied() -> bool:
    """Scenario 2: approver rejects → PermissionError raised."""
    from xruntime._runtime._middleware._approval import (
        ApprovalDecision,
        ApprovalMiddleware,
        ApprovalStrategy,
    )

    async def approver(_req: Any) -> ApprovalDecision:
        return ApprovalDecision(
            approved=False,
            reason="destructive command not allowed",
            approver="bob",
        )

    mw = ApprovalMiddleware(
        strategy=ApprovalStrategy.ALWAYS,
        approval_callback=approver,
        timeout_seconds=2.0,
    )
    try:
        async for _ in mw.on_acting(
            _Agent(),
            {"tool_call": _ToolCall("Bash", {"command": "rm -rf /"})},
            _next_handler(),
        ):
            pass
    except PermissionError as exc:
        ok = "Bash" in str(exc) and "destructive" in str(exc)
        log.info(
            "[2/5 DENIED]     %s — PermissionError: %s",
            "PASS" if ok else "FAIL",
            exc,
        )
        return ok

    log.error("[2/5 DENIED]     FAIL — no PermissionError raised")
    return False


async def scenario_timeout() -> bool:
    """Scenario 3: approver hangs → ApprovalTimeoutError raised."""
    from xruntime._runtime._middleware._approval import (
        ApprovalMiddleware,
        ApprovalStrategy,
        ApprovalTimeoutError,
    )

    async def slow_approver(_req: Any) -> ApprovalDecision:
        await asyncio.sleep(30)  # longer than timeout
        return ApprovalDecision(approved=True)

    mw = ApprovalMiddleware(
        strategy=ApprovalStrategy.ALWAYS,
        approval_callback=slow_approver,
        timeout_seconds=0.2,  # 200ms
    )
    try:
        async for _ in mw.on_acting(
            _Agent(),
            {"tool_call": _ToolCall("Write", {"path": "/tmp/x"})},
            _next_handler(),
        ):
            pass
    except ApprovalTimeoutError as exc:
        ok = "Write" in str(exc) and "0.2s" in str(exc)
        log.info(
            "[3/5 TIMEOUT]    %s — ApprovalTimeoutError: %s",
            "PASS" if ok else "FAIL",
            exc,
        )
        return ok

    log.error("[3/5 TIMEOUT]    FAIL — no ApprovalTimeoutError raised")
    return False


async def scenario_once() -> bool:
    """Scenario 4: ONCE strategy — first call blocks, second passthrough."""
    from xruntime._runtime._middleware._approval import (
        ApprovalDecision,
        ApprovalMiddleware,
        ApprovalStateCache,
        ApprovalStrategy,
    )

    call_count = 0

    async def approver(_req: Any) -> ApprovalDecision:
        nonlocal call_count
        call_count += 1
        return ApprovalDecision(approved=True, approver="carol")

    state_cache = ApprovalStateCache()
    mw = ApprovalMiddleware(
        strategy=ApprovalStrategy.ONCE,
        approval_callback=approver,
        state_cache=state_cache,
        timeout_seconds=2.0,
    )
    agent = _Agent(session_id="once-sess")

    # First call — should invoke approver
    async for _ in mw.on_acting(
        agent,
        {"tool_call": _ToolCall("Bash", {"command": "ls"})},
        _next_handler(),
    ):
        pass

    # Second call of same tool in same session — should passthrough
    async for _ in mw.on_acting(
        agent,
        {"tool_call": _ToolCall("Bash", {"command": "pwd"})},
        _next_handler(),
    ):
        pass

    ok = call_count == 1
    log.info(
        "[4/5 ONCE]       %s — approver invoked %d time(s) (expected 1)",
        "PASS" if ok else "FAIL",
        call_count,
    )
    return ok


async def scenario_predicate() -> bool:
    """Scenario 5: PREDICATE — destructive blocked, safe passthrough."""
    from xruntime._runtime._middleware._approval import (
        ApprovalDecision,
        ApprovalMiddleware,
        ApprovalStrategy,
    )

    calls: list[str] = []

    async def approver(req: Any) -> ApprovalDecision:
        calls.append(req.tool_name)
        return ApprovalDecision(approved=True)

    def is_destructive(tool_name: str, tool_input: dict) -> bool:
        return "rm" in tool_input.get("command", "")

    mw = ApprovalMiddleware(
        strategy=ApprovalStrategy.PREDICATE,
        approval_callback=approver,
        predicate=is_destructive,
        timeout_seconds=2.0,
    )
    agent = _Agent()

    # Safe command — predicate returns False, no approval needed
    async for _ in mw.on_acting(
        agent,
        {"tool_call": _ToolCall("Bash", {"command": "ls"})},
        _next_handler(),
    ):
        pass

    # Destructive command — predicate returns True, approval required
    async for _ in mw.on_acting(
        agent,
        {"tool_call": _ToolCall("Bash", {"command": "rm -rf /tmp"})},
        _next_handler(),
    ):
        pass

    ok = calls == ["Bash"]
    log.info(
        "[5/5 PREDICATE]  %s — approver invoked for: %s (expected ['Bash'])",
        "PASS" if ok else "FAIL",
        calls,
    )
    return ok


# ── main ────────────────────────────────────────────────────────────


async def main() -> int:
    """Run all scenarios; return exit code (0 = all pass)."""
    log.info("=" * 60)
    log.info("ApprovalMiddleware exception-handling verification")
    log.info("=" * 60)

    results = await asyncio.gather(
        scenario_approved(),
        scenario_denied(),
        scenario_timeout(),
        scenario_once(),
        scenario_predicate(),
    )

    log.info("=" * 60)
    passed = sum(results)
    total = len(results)
    log.info("Result: %d/%d scenarios passed", passed, total)
    log.info("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
