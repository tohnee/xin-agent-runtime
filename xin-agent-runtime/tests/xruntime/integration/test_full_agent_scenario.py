# -*- coding: utf-8 -*-
"""End-to-end test: full runtime with all modules integrated.

Verifies that create_xruntime_extension returns all new modules
and that they work together in a complete Agent execution scenario.
"""
from __future__ import annotations

import asyncio

import pytest

from xruntime._config import XRuntimeConfig
from xruntime._gateway._extension import create_xruntime_extension
from xruntime._runtime._memory._hybrid_retriever import HybridRetriever
from xruntime._runtime._memory._models import MemoryItem
from xruntime._runtime._subagents import (
    SubAgentResult,
    SubAgentSpec,
    SubAgentTask,
    TaskTool,
)


class TestExtensionReturnsAllModules:
    """Verify create_xruntime_extension returns new modules."""

    def test_returns_skill_registry(self) -> None:
        ext = create_xruntime_extension()
        assert "skill_registry" in ext
        assert ext["skill_registry"] is not None

    def test_returns_memory_store(self) -> None:
        ext = create_xruntime_extension()
        assert "memory_store" in ext
        assert ext["memory_store"] is not None

    def test_returns_subagent_executor(self) -> None:
        ext = create_xruntime_extension()
        assert "subagent_executor" in ext
        assert ext["subagent_executor"] is not None

    def test_skill_registry_has_skills(self) -> None:
        ext = create_xruntime_extension()
        names = ext["skill_registry"].skill_names
        # Should discover built-in skills (research, coding, data-analysis)
        assert len(names) >= 1

    def test_middleware_factory_still_works(self) -> None:
        ext = create_xruntime_extension()

        async def check():
            mws = await ext["extra_agent_middlewares"](
                "alice",
                "agent-1",
                "sess-1",
            )
            return len(mws)

        count = asyncio.run(check())
        assert count == 9


class TestFullAgentScenario:
    """Complete Agent execution scenario with all modules."""

    @pytest.mark.asyncio
    async def test_complete_research_workflow(self, tmp_path) -> None:
        """Simulate: user asks research question → Agent uses skills
        → delegates to sub-agents → stores findings in memory →
        retrieves relevant memories → all wrapped in middleware chain.
        """
        # 1. Create extension with all modules
        ext = create_xruntime_extension()
        skill_registry = ext["skill_registry"]
        memory_store = ext["memory_store"]
        executor = ext["subagent_executor"]
        mw_factory = ext["extra_agent_middlewares"]

        # 2. Verify middleware chain
        middlewares = await mw_factory("alice", "research-agent", "sess-1")
        mw_names = [type(mw).__name__ for mw in middlewares]
        assert "LangfuseTracerMiddleware" in mw_names
        assert "LoopDetectionMiddleware" in mw_names
        assert "LLMErrorHandlingMiddleware" in mw_names

        # 3. Verify skills discovered
        skills = skill_registry.skill_names
        assert len(skills) >= 1

        # 4. Load a skill
        first_skill = skills[0]
        content = skill_registry.load_content(first_skill)
        assert content.instructions != ""

        # 5. Inject skill list to system prompt
        skill_prompt = skill_registry.inject_to_system_prompt()
        assert "Available Skills" in skill_prompt

        # 6. Pre-populate memory with user preference
        memory_store.add(
            MemoryItem(
                content="User prefers Python and concise summaries",
                user_id="alice",
                tenant_id="default",
                type="preference",
                tags=["python", "preference", "summary"],
                confidence=0.9,
            ),
        )

        # 7. Retrieve memories using hybrid search
        retriever = HybridRetriever(memory_store)
        memories = retriever.search(
            query="Python preference summary",
            user_id="alice",
            tenant_id="default",
        )
        assert len(memories) >= 1
        assert "Python" in memories[0].content

        # 8. Add a sub-agent spec
        executor.add_spec(
            SubAgentSpec(
                name="researcher",
                description="Research specialist",
                system_prompt="You are a research expert.",
            ),
        )

        # 9. Delegate task via TaskTool
        async def runner(spec, task):
            # Sub-agent can access skills
            skill_content = skill_registry.load_content(first_skill)
            # Sub-agent stores finding to memory
            memory_store.add(
                MemoryItem(
                    content=f"Research finding: {task.objective}",
                    user_id="alice",
                    tenant_id="default",
                    type="fact",
                    tags=["research", "finding"],
                    confidence=0.85,
                ),
            )
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary=f"Researched: {task.objective}",
                findings=[f"Found info about {task.objective}"],
            )

        tool = TaskTool(executor, default_runner=runner)
        result = await tool(
            subagent="researcher",
            description="Python ecosystem trends 2026",
        )

        # 10. Verify task completed
        assert result["success"] is True
        assert "Researched" in result["summary"]

        # 11. Verify finding stored in memory
        all_memories = memory_store.list_all(
            user_id="alice",
            tenant_id="default",
        )
        assert len(all_memories) == 2  # preference + finding
        assert any("Research finding" in m.content for m in all_memories)

        # 12. Verify hybrid search finds both
        results = retriever.search(
            query="Python research",
            user_id="alice",
            tenant_id="default",
            top_k=10,
        )
        assert len(results) >= 2

        # 13. Verify executor stats
        assert executor.stats["total_executed"] == 1
        assert executor.stats["total_succeeded"] == 1

        # 14. Verify Langfuse tracer is no-op (not configured)
        state_cache = ext["middleware_state_cache"]
        exporter = await state_cache.get_langfuse_exporter()
        assert exporter.is_noop is True

    @pytest.mark.asyncio
    async def test_loop_detection_in_middleware_chain(self) -> None:
        """LoopDetection middleware is in the chain and functional."""
        ext = create_xruntime_extension()
        middlewares = await ext["extra_agent_middlewares"](
            "alice",
            "agent-1",
            "sess-1",
        )

        from xruntime._runtime._middleware._loop_detection import (
            LoopDetectionMiddleware,
        )

        loop_mw = next(
            mw for mw in middlewares if isinstance(mw, LoopDetectionMiddleware)
        )
        assert len(loop_mw.history) == 0  # Fresh

    @pytest.mark.asyncio
    async def test_error_handling_circuit_closed(self) -> None:
        """LLMErrorHandling circuit starts in CLOSED state."""
        ext = create_xruntime_extension()
        middlewares = await ext["extra_agent_middlewares"](
            "alice",
            "agent-1",
            "sess-1",
        )

        from xruntime._runtime._middleware._llm_error_handling import (
            CircuitState,
            LLMErrorHandlingMiddleware,
        )

        error_mw = next(
            mw
            for mw in middlewares
            if isinstance(mw, LLMErrorHandlingMiddleware)
        )
        assert error_mw.circuit_state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_memory_tenant_isolation_in_extension(self) -> None:
        """Memory store from extension supports tenant isolation."""
        ext = create_xruntime_extension()
        store = ext["memory_store"]

        store.add(
            MemoryItem(
                content="ACME secret data",
                user_id="alice",
                tenant_id="acme",
            ),
        )
        store.add(
            MemoryItem(
                content="OtherCorp data",
                user_id="bob",
                tenant_id="othercorp",
            ),
        )

        acme = store.search("secret", user_id="alice", tenant_id="acme")
        other = store.search("data", user_id="bob", tenant_id="othercorp")
        cross = store.search("secret", user_id="bob", tenant_id="othercorp")

        assert len(acme) == 1
        assert len(other) == 1
        assert cross == []  # No cross-tenant leakage
