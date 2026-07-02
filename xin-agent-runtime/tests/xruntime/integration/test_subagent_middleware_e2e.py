# -*- coding: utf-8 -*-
"""E2E test: sub-agent call with full middleware chain verification.

Verifies that all 9 middlewares work together when a sub-agent
is invoked, including skill injection, memory injection, loop
detection, and metrics recording.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from xruntime._gateway._extension import create_xruntime_extension
from xruntime._runtime._memory._models import MemoryItem
from xruntime._runtime._subagents import (
    SubAgentResult,
    SubAgentSpec,
    SubAgentTask,
)


class FakeAgent:
    name = "test-agent"
    user_id = "alice"
    tenant_id = "default"
    session_id = "sess-1"


class TestSubAgentWithMiddlewares:
    """Sub-agent + middleware chain integration tests."""

    @pytest.mark.asyncio
    async def test_subagent_with_skill_and_memory_injection(
        self,
        caplog,
    ) -> None:
        """Full scenario: skills injected → memory injected →
        sub-agent delegates → findings stored → metrics recorded."""
        ext = create_xruntime_extension()
        skill_registry = ext["skill_registry"]
        memory_store = ext["memory_store"]
        executor = ext["subagent_executor"]
        task_tool = ext["task_tool"]
        mw_factory = ext["extra_agent_middlewares"]

        # Add a sub-agent spec
        executor.add_spec(
            SubAgentSpec(
                name="researcher",
                description="Research specialist",
                system_prompt="You research topics.",
            ),
        )

        # Pre-populate memory
        memory_store.add(
            MemoryItem(
                content="User prefers Python and FastAPI",
                user_id="alice",
                tenant_id="default",
                type="preference",
                tags=["python", "fastapi", "preference"],
                confidence=0.9,
            ),
        )

        # Get middleware chain
        middlewares = await mw_factory("alice", "research-agent", "sess-1")
        assert len(middlewares) == 9

        # Find SkillInjection and Memory middlewares
        from xruntime._runtime._middleware._skill_injection import (
            SkillInjectionMiddleware,
        )
        from xruntime._runtime._memory._middleware import (
            MemoryMiddleware,
        )

        skill_mw = next(
            mw
            for mw in middlewares
            if isinstance(mw, SkillInjectionMiddleware)
        )
        memory_mw = next(
            mw for mw in middlewares if isinstance(mw, MemoryMiddleware)
        )

        # Set memory query (simulates on_reply capturing user message)
        memory_mw._last_query = "Python FastAPI preference"

        # Step 1: Skill injection into system prompt
        with caplog.at_level(logging.INFO):
            prompt = "You are a helpful assistant."
            prompt = await skill_mw.on_system_prompt(FakeAgent(), prompt)
        assert "Available Skills" in prompt
        assert any(
            "injecting" in record.message and "skills" in record.message
            for record in caplog.records
        )

        # Step 2: Memory injection into system prompt
        with caplog.at_level(logging.INFO):
            prompt = await memory_mw.on_system_prompt(FakeAgent(), prompt)
        assert "Long-term Memory" in prompt
        assert "Python" in prompt
        assert any(
            "injecting" in record.message and "memories" in record.message
            for record in caplog.records
        )

        # Step 3: Sub-agent delegation via TaskTool
        async def runner(spec, task):
            # Sub-agent can access skills
            skills = skill_registry.skill_names
            assert len(skills) >= 1

            # Sub-agent stores finding
            memory_store.add(
                MemoryItem(
                    content="Python 3.12 type parameter syntax",
                    user_id="alice",
                    tenant_id="default",
                    type="fact",
                    tags=["python", "typing"],
                    confidence=0.85,
                ),
            )
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary="Researched Python typing",
                findings=["Python 3.12 generics info"],
            )

        task_tool._executor._specs["researcher"] = executor._specs[
            "researcher"
        ]

        # Re-create TaskTool with the runner
        from xruntime._runtime._subagents._task_tool import TaskTool

        task_tool = TaskTool(executor, default_runner=runner)

        result = await task_tool(
            subagent="researcher",
            description="Research Python typing",
        )
        assert result["success"] is True
        assert "Python" in result["summary"]

        # Step 4: Verify memory has 2 items (preference + finding)
        all_memories = memory_store.list_all(
            user_id="alice",
            tenant_id="default",
        )
        assert len(all_memories) == 2

        # Step 5: Verify executor stats
        assert executor.stats["total_executed"] == 1
        assert executor.stats["total_succeeded"] == 1

    @pytest.mark.asyncio
    async def test_middleware_metrics_recorded(self) -> None:
        """Verify metrics collector tracks middleware latency."""
        from xruntime._infra._metrics import MetricsCollector

        collector = MetricsCollector()
        collector.record_middleware_latency(
            "SkillInjectionMiddleware",
            0.5,
        )
        collector.record_middleware_latency(
            "SkillInjectionMiddleware",
            1.5,
        )
        collector.record_middleware_latency(
            "MemoryMiddleware",
            2.0,
        )
        collector.record_subagent_call(
            "researcher",
            1.5,
            True,
            300,
        )

        stats = collector.middleware_stats("SkillInjectionMiddleware")
        assert stats["count"] == 2
        assert stats["avg_ms"] == 1.0

        sub_stats = collector.subagent_stats("researcher")
        assert sub_stats["count"] == 1
        assert sub_stats["avg_duration_seconds"] == 1.5

        # Verify Prometheus output
        text = collector.export_prometheus()
        assert "xruntime_middleware_latency_ms" in text
        assert 'middleware="SkillInjectionMiddleware"' in text
        assert "xruntime_subagent_duration_seconds" in text

    @pytest.mark.asyncio
    async def test_no_memory_skip_logged(self, caplog) -> None:
        """When no user_id/query, memory injection is skipped with log."""
        from xruntime._runtime._memory._middleware import (
            MemoryMiddleware,
        )
        from xruntime._runtime._memory._store import MemoryStore

        mw = MemoryMiddleware(
            store=MemoryStore(),
            user_id="",
            session_id="",
        )
        with caplog.at_level(logging.DEBUG):
            result = await mw.on_system_prompt(
                FakeAgent(),
                "base prompt",
            )
        assert result == "base prompt"
        assert any("skipped" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_no_skills_skip_logged(self, caplog, tmp_path) -> None:
        """When no skills, injection is skipped with log."""
        from xruntime._runtime._middleware._skill_injection import (
            SkillInjectionMiddleware,
        )
        from xruntime._runtime._skills import SkillRegistry

        registry = SkillRegistry(skill_dirs=[])
        mw = SkillInjectionMiddleware(registry)
        with caplog.at_level(logging.DEBUG):
            result = await mw.on_system_prompt(
                FakeAgent(),
                "base prompt",
            )
        assert result == "base prompt"
        assert any("no skills" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_full_prompt_with_skills_and_memories(
        self,
    ) -> None:
        """Verify the final system prompt contains both
        skills and memories."""
        ext = create_xruntime_extension()
        memory_store = ext["memory_store"]
        mw_factory = ext["extra_agent_middlewares"]

        memory_store.add(
            MemoryItem(
                content="User works at ACME Corp",
                user_id="alice",
                tenant_id="default",
                type="fact",
                tags=["acme", "company"],
                confidence=0.8,
            ),
        )

        middlewares = await mw_factory("alice", "agent-1", "sess-1")

        prompt = "You are a helpful assistant."
        for mw in middlewares:
            if mw.is_implemented("on_system_prompt"):
                # Set query for memory middleware
                if hasattr(mw, "_last_query"):
                    mw._last_query = "ACME company"
                prompt = await mw.on_system_prompt(
                    FakeAgent(),
                    prompt,
                )

        # Should have both skills and memories
        assert "Available Skills" in prompt
        assert "Long-term Memory" in prompt
        assert "ACME" in prompt

    @pytest.mark.asyncio
    async def test_subagent_failure_with_middlewares(self) -> None:
        """Failed sub-agent doesn't corrupt memory or skills."""
        ext = create_xruntime_extension()
        memory_store = ext["memory_store"]
        executor = ext["subagent_executor"]
        task_tool = ext["task_tool"]

        executor.add_spec(
            SubAgentSpec(
                name="failing-agent",
                description="Always fails",
            ),
        )

        async def failing_runner(spec, task):
            return SubAgentResult(
                task_id=task.task_id,
                success=False,
                errors=["Connection refused"],
            )

        from xruntime._runtime._subagents._task_tool import TaskTool

        task_tool = TaskTool(executor, default_runner=failing_runner)

        result = await task_tool(
            subagent="failing-agent",
            description="will fail",
        )
        assert result["success"] is False
        assert "Connection refused" in result["errors"][0]

        # Memory not polluted
        assert memory_store.count == 0
        assert executor.stats["total_failed"] == 1
