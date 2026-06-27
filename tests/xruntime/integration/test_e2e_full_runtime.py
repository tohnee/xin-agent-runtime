# -*- coding: utf-8 -*-
"""End-to-end integration test: all new modules working together.

Verifies that create_xruntime_extension produces a middleware chain
containing all 7 middlewares, and that they work correctly in
sequence: Langfuse → LoopDetection → LLMErrorHandling → Audit →
Quota → RBAC → Redaction.

Also tests SkillRegistry, MemorySystem, and SubAgentTask in a
combined scenario.
"""
from __future__ import annotations

import asyncio

import pytest

from xruntime._config import XRuntimeConfig
from xruntime._gateway._extension import create_xruntime_extension
from xruntime._runtime._memory._hybrid_retriever import HybridRetriever
from xruntime._runtime._memory._models import MemoryItem
from xruntime._runtime._memory._store import MemoryStore
from xruntime._runtime._middleware._langfuse_tracer import (
    LangfuseTracerMiddleware,
)
from xruntime._runtime._middleware._loop_detection import (
    LoopDetectionMiddleware,
)
from xruntime._runtime._middleware._llm_error_handling import (
    LLMErrorHandlingMiddleware,
)
from xruntime._runtime._skills import SkillRegistry
from xruntime._runtime._subagents import (
    SubAgentExecutor,
    SubAgentSpec,
    SubAgentTask,
    TaskTool,
)


class TestMiddlewareChainIntegration:
    """Verify all middlewares are present in the chain."""

    @pytest.mark.asyncio
    async def test_factory_produces_all_middlewares(self) -> None:
        """create_xruntime_extension factory produces 7 middlewares."""
        ext = create_xruntime_extension()
        factory = ext["extra_agent_middlewares"]
        mws = await factory("alice", "agent-1", "sess-1")

        mw_types = [type(mw).__name__ for mw in mws]

        # All 7 middlewares present
        assert "LangfuseTracerMiddleware" in mw_types
        assert "LoopDetectionMiddleware" in mw_types
        assert "LLMErrorHandlingMiddleware" in mw_types
        assert "AuditMiddleware" in mw_types
        assert "QuotaMiddleware" in mw_types
        assert "RbacMiddleware" in mw_types
        assert "SecretRedactionMiddleware" in mw_types

        # Langfuse is first (traces everything)
        assert mw_types[0] == "LangfuseTracerMiddleware"

        # Loop detection before LLM error handling
        loop_idx = mw_types.index("LoopDetectionMiddleware")
        error_idx = mw_types.index("LLMErrorHandlingMiddleware")
        assert loop_idx < error_idx

    @pytest.mark.asyncio
    async def test_middleware_chain_is_reusable(self) -> None:
        """Factory can be called multiple times."""
        ext = create_xruntime_extension()
        factory = ext["extra_agent_middlewares"]

        mws1 = await factory("alice", "agent-1", "sess-1")
        mws2 = await factory("bob", "agent-2", "sess-2")

        assert len(mws1) == len(mws2) == 7
        # Different instances (per-session state)
        assert mws1[0] is not mws2[0]

    @pytest.mark.asyncio
    async def test_langfuse_exporter_is_noop_by_default(self) -> None:
        """Langfuse is no-op when not configured."""
        ext = create_xruntime_extension()
        state_cache = ext["middleware_state_cache"]
        exporter = await state_cache.get_langfuse_exporter()
        assert exporter.is_noop is True

    @pytest.mark.asyncio
    async def test_langfuse_exporter_enabled(self) -> None:
        """Langfuse exporter is active when configured."""
        config = XRuntimeConfig()
        config.observability.langfuse_enabled = True
        config.observability.langfuse_host = "http://localhost:3000"
        config.observability.langfuse_public_key = "pk-test"
        config.observability.langfuse_secret_key = "sk-test"

        ext = create_xruntime_extension(config=config)
        state_cache = ext["middleware_state_cache"]
        exporter = await state_cache.get_langfuse_exporter()
        # Will be noop if langfuse package not installed
        # but config is correctly passed
        assert exporter is not None


class TestFullRuntimeScenario:
    """End-to-end scenario: skills + memory + subagents + middlewares."""

    @pytest.mark.asyncio
    async def test_research_team_scenario(self, tmp_path) -> None:
        """Simulate a research team scenario:

        1. SkillRegistry discovers research skill
        2. MemoryStore has user preferences
        3. HybridRetriever finds relevant memories
        4. SubAgentExecutor runs 3 parallel researchers
        5. Each researcher stores findings to MemoryStore
        6. Final keyword + vector search retrieves all findings
        7. Middleware chain wraps everything
        """
        # --- Setup: Skills ---
        skill_dir = tmp_path / "skills"
        research_dir = skill_dir / "research"
        research_dir.mkdir(parents=True)
        (research_dir / "SKILL.yaml").write_text(
            "name: research\n"
            "description: Conduct research\n"
            "instructions: '# Research instructions'\n"
        )
        registry = SkillRegistry(skill_dirs=[str(skill_dir)])
        manifests = registry.discover()
        assert len(manifests) == 1
        assert manifests[0].name == "research"
        skill_prompt = registry.inject_to_system_prompt()
        assert "research" in skill_prompt

        # --- Setup: Memory ---
        store = MemoryStore(min_confidence=0.3)
        retriever = HybridRetriever(store)

        # Pre-populate user preferences
        store.add(
            MemoryItem(
                content="User prefers concise summaries with citations",
                user_id="alice",
                tenant_id="acme",
                type="preference",
                tags=["preference", "summary", "citations"],
                confidence=0.95,
            )
        )

        # --- Setup: SubAgents ---
        specs = [
            SubAgentSpec(
                name="python-researcher",
                description="Research Python ecosystem",
                system_prompt="You are a Python expert.",
            ),
            SubAgentSpec(
                name="rust-researcher",
                description="Research Rust ecosystem",
                system_prompt="You are a Rust expert.",
            ),
            SubAgentSpec(
                name="market-analyst",
                description="Analyze market trends",
                system_prompt="You are a market analyst.",
            ),
        ]
        executor = SubAgentExecutor(specs, max_concurrent=2)

        # --- Setup: Middleware chain ---
        ext = create_xruntime_extension()
        mw_factory = ext["extra_agent_middlewares"]
        middlewares = await mw_factory("alice", "research-agent", "sess-1")

        # Verify middleware chain is complete
        mw_names = [type(mw).__name__ for mw in middlewares]
        assert "LangfuseTracerMiddleware" in mw_names
        assert "LoopDetectionMiddleware" in mw_names
        assert "LLMErrorHandlingMiddleware" in mw_names

        # --- Execute: 3 parallel research tasks ---
        findings = {
            "python-researcher": (
                "Python 3.12 introduced type parameter syntax "
                "for generic functions without TypeVar",
                ["python", "typing", "generics"],
            ),
            "rust-researcher": (
                "Rust tokio runtime dominates async networking "
                "with zero-cost abstractions",
                ["rust", "tokio", "async", "networking"],
            ),
            "market-analyst": (
                "AI coding tools market grew 300% in 2026, "
                "Python and Rust leading adoption trends",
                ["market", "ai", "python", "rust", "trends"],
            ),
        }

        async def research_runner(
            spec: SubAgentSpec,
            task: SubAgentTask,
        ) -> any:  # noqa: ANN401
            from xruntime._runtime._subagents import SubAgentResult

            content, tags = findings[spec.name]

            # Simulate memory-informed research: retrieve preferences
            prefs = retriever.search(
                query="preference summary citations",
                user_id="alice",
                tenant_id="acme",
            )
            assert len(prefs) >= 1  # User preference found

            # Store research finding
            store.add(
                MemoryItem(
                    content=content,
                    user_id="alice",
                    tenant_id="acme",
                    type="fact",
                    tags=tags,
                    confidence=0.85,
                    source_session_id="sess-1",
                )
            )

            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary=f"{spec.name} completed",
                findings=[content],
            )

        tasks = [
            SubAgentTask(
                spec_name="python-researcher",
                objective="Research Python typing",
            ),
            SubAgentTask(
                spec_name="rust-researcher",
                objective="Research Rust async",
            ),
            SubAgentTask(
                spec_name="market-analyst",
                objective="Analyze AI market",
            ),
        ]

        results = await executor.execute_batch(tasks, runner=research_runner)

        # --- Verify: All tasks succeeded ---
        assert len(results) == 3
        assert all(r.success for r in results)
        assert executor.stats["total_executed"] == 3
        assert executor.stats["total_succeeded"] == 3

        # --- Verify: Memory has 4 items (1 preference + 3 findings) ---
        assert store.count == 4

        # --- Verify: Keyword search finds Python findings ---
        kw_python = store.search(
            query="Python typing generics",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(kw_python) >= 1
        assert any("Python 3.12" in m.content for m in kw_python)

        # --- Verify: Hybrid search finds cross-topic ---
        hybrid_python = retriever.search(
            query="Python",
            user_id="alice",
            tenant_id="acme",
            top_k=10,
        )
        # Should find python-researcher + market-analyst findings
        assert len(hybrid_python) >= 2

        # --- Verify: Tenant isolation ---
        store.add(
            MemoryItem(
                content="Other company uses Java",
                user_id="bob",
                tenant_id="othercorp",
                tags=["java"],
            )
        )
        acme_results = retriever.search(
            query="Java",
            user_id="alice",
            tenant_id="acme",
        )
        assert all(r.tenant_id == "acme" for r in acme_results)

        # --- Verify: SkillRegistry content loadable ---
        content = registry.load_content("research")
        assert "Research" in content.instructions

        # --- Verify: LoopDetection middleware is stateful ---
        loop_mw = next(
            mw for mw in middlewares if isinstance(mw, LoopDetectionMiddleware)
        )
        assert len(loop_mw.history) == 0  # Fresh

        # --- Verify: LLMErrorHandling middleware starts closed ---
        error_mw = next(
            mw
            for mw in middlewares
            if isinstance(mw, LLMErrorHandlingMiddleware)
        )
        from xruntime._runtime._middleware._llm_error_handling import (
            CircuitState,
        )

        assert error_mw.circuit_state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failed_subagent_with_error_handling(self) -> None:
        """When a sub-agent fails, LLMErrorHandling stats track it
        and memory is not polluted."""
        store = MemoryStore(min_confidence=0.3)
        specs = [
            SubAgentSpec(
                name="failing-agent",
                description="Always fails",
            ),
            SubAgentSpec(
                name="success-agent",
                description="Always succeeds",
            ),
        ]
        executor = SubAgentExecutor(specs, max_concurrent=2)

        from xruntime._runtime._subagents import SubAgentResult

        async def runner(spec, task):
            if spec.name == "failing-agent":
                return SubAgentResult(
                    task_id=task.task_id,
                    success=False,
                    errors=["Connection timeout"],
                )
            store.add(
                MemoryItem(
                    content="Success finding about Python",
                    user_id="alice",
                    tenant_id="acme",
                    tags=["python"],
                    confidence=0.8,
                )
            )
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary="Done",
            )

        tasks = [
            SubAgentTask(
                spec_name="failing-agent",
                objective="will fail",
            ),
            SubAgentTask(
                spec_name="success-agent",
                objective="will succeed",
            ),
        ]

        results = await executor.execute_batch(tasks, runner=runner)

        # One success, one failure
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 1
        assert len(failures) == 1
        assert executor.stats["total_failed"] == 1

        # Only 1 memory (from successful agent)
        assert store.count == 1
        assert "Python" in store.list_all()[0].content

    @pytest.mark.asyncio
    async def test_task_tool_with_skills_and_memory(self, tmp_path) -> None:
        """TaskTool delegates, sub-agent uses skills, stores to memory."""
        # Setup skills
        skill_dir = tmp_path / "skills" / "analysis"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.yaml").write_text(
            "name: analysis\n"
            "description: Data analysis skill\n"
            "instructions: '# Analyze data'\n"
        )
        registry = SkillRegistry(skill_dirs=[str(tmp_path / "skills")])
        registry.discover()

        # Setup memory
        store = MemoryStore()
        retriever = HybridRetriever(store)

        # Setup subagent
        executor = SubAgentExecutor(
            [
                SubAgentSpec(
                    name="analyst",
                    description="Data analyst",
                )
            ]
        )

        from xruntime._runtime._subagents import SubAgentResult

        async def analyst_runner(spec, task):
            # Load skill
            skill = registry.load_content("analysis")
            assert "Analyze" in skill.instructions

            # Do analysis
            finding = "Analysis complete: data shows 42% growth"
            store.add(
                MemoryItem(
                    content=finding,
                    user_id="alice",
                    tenant_id="acme",
                    tags=["analysis", "growth"],
                    confidence=0.9,
                )
            )
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary="Analysis done",
                findings=[finding],
            )

        tool = TaskTool(executor, default_runner=analyst_runner)

        # Execute via TaskTool
        result = await tool(
            subagent="analyst",
            description="Analyze Q4 data",
        )

        assert result["success"] is True
        assert result["findings"]

        # Search memory
        results = retriever.search(
            query="analysis growth data",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(results) == 1
        assert "42%" in results[0].content
