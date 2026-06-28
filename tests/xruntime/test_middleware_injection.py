# -*- coding: utf-8 -*-
"""Tests for SkillInjection and Memory middleware integration."""
from __future__ import annotations

import asyncio

import pytest

from xruntime._gateway._extension import create_xruntime_extension
from xruntime._runtime._memory._middleware import MemoryMiddleware
from xruntime._runtime._memory._models import MemoryItem
from xruntime._runtime._memory._store import MemoryStore
from xruntime._runtime._middleware._skill_injection import (
    SkillInjectionMiddleware,
)
from xruntime._runtime._skills import SkillRegistry


class FakeAgent:
    name = "test-agent"


class TestSkillInjectionMiddleware:
    """SkillInjectionMiddleware tests."""

    @pytest.mark.asyncio
    async def test_injects_skill_list(self, tmp_path) -> None:
        """on_system_prompt appends skill list."""
        d = tmp_path / "skills" / "research"
        d.mkdir(parents=True)
        (d / "SKILL.yaml").write_text(
            "name: research\ndescription: Do research\n"
        )
        registry = SkillRegistry(skill_dirs=[str(tmp_path / "skills")])
        registry.discover()

        mw = SkillInjectionMiddleware(registry)
        result = await mw.on_system_prompt(FakeAgent(), "You are helpful.")
        assert "Available Skills" in result
        assert "research" in result
        assert "load_skill" in result

    @pytest.mark.asyncio
    async def test_no_skills_returns_unchanged(self) -> None:
        """Empty registry returns prompt unchanged."""
        registry = SkillRegistry(skill_dirs=[])
        mw = SkillInjectionMiddleware(registry)
        prompt = "You are helpful."
        result = await mw.on_system_prompt(FakeAgent(), prompt)
        assert result == prompt


class TestMemoryMiddlewareIntegration:
    """MemoryMiddleware tests with sync store."""

    @pytest.mark.asyncio
    async def test_inject_memories_to_prompt(self) -> None:
        """on_system_prompt injects matching memories."""
        store = MemoryStore()
        store.add(
            MemoryItem(
                content="User prefers Python and concise summaries",
                user_id="alice",
                tenant_id="acme",
                type="preference",
                tags=["python", "preference"],
                confidence=0.9,
            )
        )
        mw = MemoryMiddleware(
            store=store,
            user_id="alice",
            tenant_id="acme",
            session_id="sess-1",
        )
        mw._last_query = "Python preference"

        result = await mw.on_system_prompt(FakeAgent(), "You are helpful.")
        assert "Long-term Memory" in result
        assert "Python" in result
        assert "preference" in result

    @pytest.mark.asyncio
    async def test_no_query_returns_unchanged(self) -> None:
        """Without a query, prompt is unchanged."""
        store = MemoryStore()
        mw = MemoryMiddleware(store=store, user_id="alice")
        prompt = "You are helpful."
        result = await mw.on_system_prompt(FakeAgent(), prompt)
        assert result == prompt

    @pytest.mark.asyncio
    async def test_no_user_returns_unchanged(self) -> None:
        """Without user_id, prompt is unchanged."""
        store = MemoryStore()
        mw = MemoryMiddleware(store=store, user_id="")
        mw._last_query = "test"
        prompt = "You are helpful."
        result = await mw.on_system_prompt(FakeAgent(), prompt)
        assert result == prompt

    @pytest.mark.asyncio
    async def test_low_confidence_filtered(self) -> None:
        """Low-confidence memories are not injected."""
        store = MemoryStore()
        store.add(
            MemoryItem(
                content="Low confidence Python fact",
                user_id="alice",
                tenant_id="acme",
                confidence=0.1,
            )
        )
        mw = MemoryMiddleware(
            store=store,
            user_id="alice",
            tenant_id="acme",
            confidence_threshold=0.5,
        )
        mw._last_query = "Python"
        result = await mw.on_system_prompt(FakeAgent(), "Base prompt.")
        assert "Low confidence" not in result


class TestFullChainWithNewMiddlewares:
    """Verify full middleware chain has 9 middlewares with correct hooks."""

    @pytest.mark.asyncio
    async def test_chain_has_9_middlewares(self) -> None:
        """Extension produces 9 middlewares."""
        ext = create_xruntime_extension()
        mws = await ext["extra_agent_middlewares"](
            "alice", "agent-1", "sess-1"
        )
        assert len(mws) == 9

    @pytest.mark.asyncio
    async def test_skill_injection_in_chain(self) -> None:
        """SkillInjectionMiddleware is in the chain."""
        ext = create_xruntime_extension()
        mws = await ext["extra_agent_middlewares"](
            "alice", "agent-1", "sess-1"
        )
        types = [type(mw).__name__ for mw in mws]
        assert "SkillInjectionMiddleware" in types

    @pytest.mark.asyncio
    async def test_memory_middleware_in_chain(self) -> None:
        """MemoryMiddleware is in the chain."""
        ext = create_xruntime_extension()
        mws = await ext["extra_agent_middlewares"](
            "alice", "agent-1", "sess-1"
        )
        types = [type(mw).__name__ for mw in mws]
        assert "MemoryMiddleware" in types

    @pytest.mark.asyncio
    async def test_on_system_prompt_hooks_present(self) -> None:
        """At least 2 middlewares implement on_system_prompt."""
        ext = create_xruntime_extension()
        mws = await ext["extra_agent_middlewares"](
            "alice", "agent-1", "sess-1"
        )
        prompt_hooks = [
            mw for mw in mws if mw.is_implemented("on_system_prompt")
        ]
        assert len(prompt_hooks) >= 2  # Skill + Memory

    @pytest.mark.asyncio
    async def test_skill_prompt_actually_injected(self) -> None:
        """Calling on_system_prompt on the chain produces skill text."""
        ext = create_xruntime_extension()
        mws = await ext["extra_agent_middlewares"](
            "alice", "agent-1", "sess-1"
        )
        prompt = "You are a helpful assistant."
        for mw in mws:
            if mw.is_implemented("on_system_prompt"):
                prompt = await mw.on_system_prompt(FakeAgent(), prompt)
        assert "Available Skills" in prompt
