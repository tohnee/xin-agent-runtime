# -*- coding: utf-8 -*-
"""Integration test: SubAgentTask + MemorySystem combined scenario.

Verifies that sub-agent parallel execution and memory keyword
search work correctly together in a realistic multi-tenant
scenario.
"""
from __future__ import annotations

import asyncio

import pytest

from xruntime._runtime._memory._models import MemoryItem
from xruntime._runtime._memory._store import MemoryStore
from xruntime._runtime._subagents import (
    SubAgentExecutor,
    SubAgentResult,
    SubAgentSpec,
    SubAgentTask,
)
from xruntime._runtime._subagents._task_tool import TaskTool


# ------------------------------------------------------------------ #
#  Scenario: A research team with 3 sub-agents working in parallel   #
#  on different topics. Each sub-agent stores findings as memories.   #
#  Then we verify keyword search retrieves the correct findings.      #
# ------------------------------------------------------------------ #


@pytest.fixture
def research_specs() -> list[SubAgentSpec]:
    """Three specialist sub-agents."""
    return [
        SubAgentSpec(
            name="python-researcher",
            description="Research Python ecosystem",
            system_prompt="You research Python tools and frameworks.",
            allowed_tools=["web_search", "read_file"],
            max_turns=5,
        ),
        SubAgentSpec(
            name="rust-researcher",
            description="Research Rust ecosystem",
            system_prompt="You research Rust tools and frameworks.",
            allowed_tools=["web_search", "read_file"],
            max_turns=5,
        ),
        SubAgentSpec(
            name="market-analyst",
            description="Analyze market trends",
            system_prompt="You analyze market data and trends.",
            allowed_tools=["web_search", "read_file", "write_file"],
            max_turns=8,
        ),
    ]


@pytest.fixture
def store() -> MemoryStore:
    """Fresh memory store for each test."""
    return MemoryStore(min_confidence=0.3)


@pytest.fixture
def executor(
    research_specs: list[SubAgentSpec],
) -> SubAgentExecutor:
    return SubAgentExecutor(research_specs, max_concurrent=2)


class TestSubAgentMemoryIntegration:
    """Combined SubAgent + Memory integration tests."""

    @pytest.mark.asyncio
    async def test_parallel_subagents_store_findings_to_memory(
        self,
        executor: SubAgentExecutor,
        store: MemoryStore,
    ) -> None:
        """3 sub-agents run in parallel, each stores findings as
        memories. Verify all memories are searchable afterwards."""
        findings_data = {
            "python-researcher": (
                "Python 3.12 introduced performance improvements "
                "and better error messages for debugging",
                ["python", "performance", "debugging"],
            ),
            "rust-researcher": (
                "Rust async runtime tokio remains the dominant "
                "choice for high-performance networking",
                ["rust", "tokio", "async", "networking"],
            ),
            "market-analyst": (
                "AI coding tools market grew 300% in 2026 with "
                "Python and Rust leading adoption",
                ["market", "ai", "coding", "python", "rust"],
            ),
        }

        async def runner(
            spec: SubAgentSpec,
            task: SubAgentTask,
        ) -> SubAgentResult:
            content, tags = findings_data[spec.name]
            # Simulate storing a finding as a memory
            store.add(
                MemoryItem(
                    content=content,
                    user_id="alice",
                    tenant_id="acme",
                    type="fact",
                    tags=tags,
                    confidence=0.85,
                    source_session_id=task.parent_session_id or "sess-1",
                )
            )
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary=f"{spec.name} completed research",
                findings=[content],
            )

        # Create 3 tasks for parallel execution
        tasks = [
            SubAgentTask(
                spec_name="python-researcher",
                objective="Research Python 3.12 features",
                parent_session_id="sess-1",
            ),
            SubAgentTask(
                spec_name="rust-researcher",
                objective="Research Rust async ecosystem",
                parent_session_id="sess-1",
            ),
            SubAgentTask(
                spec_name="market-analyst",
                objective="Analyze AI coding tools market",
                parent_session_id="sess-1",
            ),
        ]

        # Execute all 3 in parallel (max_concurrent=2, so 2+1 batches)
        results = await executor.execute_batch(tasks, runner=runner)

        # All should succeed
        assert len(results) == 3
        assert all(r.success for r in results)

        # All 3 memories should be stored
        assert store.count == 3

        # Search for Python-related memories
        python_results = store.search(
            query="Python performance",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(python_results) >= 1
        assert any("Python" in r.content for r in python_results)

        # Search for Rust-related memories
        rust_results = store.search(
            query="Rust async tokio",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(rust_results) >= 1
        assert any("Rust" in r.content for r in rust_results)

        # Search for market-related memories
        market_results = store.search(
            query="market AI coding",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(market_results) >= 1
        assert any("market" in r.content.lower() for r in market_results)

    @pytest.mark.asyncio
    async def test_cross_topic_search_retrieves_correct_memories(
        self,
        executor: SubAgentExecutor,
        store: MemoryStore,
    ) -> None:
        """The market-analyst mentions both Python and Rust.
        Searching for 'Python' should find both the python-researcher's
        memory AND the market-analyst's memory."""
        store.add(
            MemoryItem(
                content="Python 3.12 has better error messages",
                user_id="alice",
                tenant_id="acme",
                tags=["python", "debugging"],
                confidence=0.9,
            )
        )
        store.add(
            MemoryItem(
                content="AI tools market: Python and Rust "
                "leading adoption in 2026",
                user_id="alice",
                tenant_id="acme",
                tags=["market", "ai", "python", "rust"],
                confidence=0.8,
            )
        )
        store.add(
            MemoryItem(
                content="Rust tokio is great for networking",
                user_id="alice",
                tenant_id="acme",
                tags=["rust", "tokio", "networking"],
                confidence=0.7,
            )
        )

        # Search "python" — should match 2 memories
        results = store.search(
            query="python",
            user_id="alice",
            tenant_id="acme",
            top_k=10,
        )
        assert len(results) == 2
        contents = [r.content for r in results]
        assert any("Python 3.12" in c for c in contents)
        assert any("AI tools market" in c for c in contents)

    @pytest.mark.asyncio
    async def test_tenant_isolation_across_subagents_and_memory(
        self,
        executor: SubAgentExecutor,
        store: MemoryStore,
    ) -> None:
        """Sub-agents for tenant A store memories. Tenant B
        cannot see them."""

        async def runner_a(
            spec: SubAgentSpec,
            task: SubAgentTask,
        ) -> SubAgentResult:
            store.add(
                MemoryItem(
                    content="ACME confidential: Python migration "
                    "plan approved",
                    user_id="alice",
                    tenant_id="acme",
                    tags=["python", "migration", "confidential"],
                    confidence=0.95,
                )
            )
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary="ACME research done",
            )

        # Tenant A runs a sub-agent
        await executor.execute(
            SubAgentTask(
                spec_name="python-researcher",
                objective="ACME Python migration",
            ),
            runner=runner_a,
        )

        # Manually add a tenant B memory
        store.add(
            MemoryItem(
                content="OtherCorp uses Java for backend",
                user_id="bob",
                tenant_id="othercorp",
                tags=["java", "backend"],
                confidence=0.8,
            )
        )

        # Tenant A searches — should only see ACME memory
        acme_results = store.search(
            query="python",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(acme_results) == 1
        assert "ACME" in acme_results[0].content

        # Tenant B searches — should only see OtherCorp memory
        other_results = store.search(
            query="java backend",
            user_id="bob",
            tenant_id="othercorp",
        )
        assert len(other_results) == 1
        assert "OtherCorp" in other_results[0].content

        # Tenant B searches for "python" — should find nothing
        cross_results = store.search(
            query="python",
            user_id="bob",
            tenant_id="othercorp",
        )
        assert cross_results == []

    @pytest.mark.asyncio
    async def test_concurrent_writes_no_corruption(
        self,
        executor: SubAgentExecutor,
        store: MemoryStore,
    ) -> None:
        """5 sub-agents write memories concurrently.
        All writes should be atomic — no data loss."""
        write_count = 0

        async def writer(
            spec: SubAgentSpec,
            task: SubAgentTask,
        ) -> SubAgentResult:
            nonlocal write_count
            store.add(
                MemoryItem(
                    content=f"Finding from {task.objective}",
                    user_id="alice",
                    tenant_id="acme",
                    tags=[task.objective.split()[-1].lower()],
                    confidence=0.7,
                )
            )
            write_count += 1
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
            )

        tasks = [
            SubAgentTask(
                spec_name="python-researcher",
                objective=f"Research topic{i}",
            )
            for i in range(5)
        ]

        results = await executor.execute_batch(tasks, runner=writer)

        assert len(results) == 5
        assert all(r.success for r in results)
        assert write_count == 5
        assert store.count == 5

        # Verify each memory is retrievable
        for i in range(5):
            found = store.search(
                query=f"topic{i}",
                user_id="alice",
                tenant_id="acme",
            )
            assert len(found) == 1
            assert f"topic{i}" in found[0].content

    @pytest.mark.asyncio
    async def test_memory_informed_subagent_decision(
        self,
        executor: SubAgentExecutor,
        store: MemoryStore,
    ) -> None:
        """Simulate: memory is retrieved, passed as context to
        a sub-agent, which uses it in its task."""
        # Pre-populate memory
        store.add(
            MemoryItem(
                content="User prefers using FastAPI for web APIs "
                "and pytest for testing",
                user_id="alice",
                tenant_id="acme",
                tags=["fastapi", "pytest", "preference"],
                type="preference",
                confidence=0.95,
            )
        )

        # Retrieve relevant memory
        memories = store.search(
            query="FastAPI testing preference",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(memories) == 1

        # Build a sub-agent task with the memory as input context
        memory_context = "; ".join(m.content for m in memories)
        task = SubAgentTask(
            spec_name="python-researcher",
            objective="Set up a new Python web project",
            input_context=f"User preferences: {memory_context}",
            constraints=["Follow user's preferred stack"],
        )

        received_context = ""

        async def context_aware_runner(
            spec: SubAgentSpec,
            task: SubAgentTask,
        ) -> SubAgentResult:
            nonlocal received_context
            received_context = task.input_context
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary="Project set up with FastAPI + pytest",
                findings=["FastAPI configured", "pytest configured"],
            )

        result = await executor.execute(
            task,
            runner=context_aware_runner,
        )

        assert result.success is True
        assert "FastAPI" in result.summary
        assert "FastAPI" in received_context
        assert "pytest" in received_context

    @pytest.mark.asyncio
    async def test_failed_subagent_does_not_pollute_memory(
        self,
        executor: SubAgentExecutor,
        store: MemoryStore,
    ) -> None:
        """If a sub-agent fails, it should not store incorrect
        memories. Only successful agents write to memory."""

        async def runner(
            spec: SubAgentSpec,
            task: SubAgentTask,
        ) -> SubAgentResult:
            if spec.name == "rust-researcher":
                return SubAgentResult(
                    task_id=task.task_id,
                    success=False,
                    errors=["Rust docs unavailable"],
                )
            # Only store on success
            store.add(
                MemoryItem(
                    content=f"{spec.name} found useful info",
                    user_id="alice",
                    tenant_id="acme",
                    tags=[spec.name.split("-")[0]],
                    confidence=0.8,
                )
            )
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
            )

        tasks = [
            SubAgentTask(
                spec_name="python-researcher",
                objective="Python research",
            ),
            SubAgentTask(
                spec_name="rust-researcher",
                objective="Rust research",
            ),
            SubAgentTask(
                spec_name="market-analyst",
                objective="Market analysis",
            ),
        ]

        results = await executor.execute_batch(tasks, runner=runner)

        # python + market succeeded, rust failed
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 2
        assert len(failures) == 1

        # Only 2 memories stored (not 3)
        assert store.count == 2

        # Rust memory should NOT exist
        rust_memories = store.search(
            query="rust",
            user_id="alice",
            tenant_id="acme",
        )
        assert rust_memories == []

        # Python + market memories should exist
        all_memories = store.list_all(
            user_id="alice",
            tenant_id="acme",
        )
        assert len(all_memories) == 2
        tags_flat = [t for m in all_memories for t in m.tags]
        assert "python" in tags_flat
        assert "market" in tags_flat

    @pytest.mark.asyncio
    async def test_task_tool_with_memory_end_to_end(
        self,
        executor: SubAgentExecutor,
        store: MemoryStore,
    ) -> None:
        """End-to-end: TaskTool delegates → sub-agent runs →
        result stored in memory → keyword search finds it."""

        async def runner(
            spec: SubAgentSpec,
            task: SubAgentTask,
        ) -> SubAgentResult:
            finding = (
                "Python 3.12 type parameter syntax enables "
                "generic functions without TypeVar"
            )
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary="Found Python 3.12 generics info",
                findings=[finding],
            )

        tool = TaskTool(executor, default_runner=runner)

        # Step 1: Delegate via TaskTool
        result = await tool(
            subagent="python-researcher",
            description="Research Python 3.12 generics",
        )
        assert result["success"] is True

        # Step 2: Simulate storing the finding in memory
        for finding in result.get("findings", []):
            store.add(
                MemoryItem(
                    content=finding,
                    user_id="alice",
                    tenant_id="acme",
                    tags=["python", "generics", "typing"],
                    confidence=0.9,
                    type="fact",
                )
            )

        # Step 3: Search memory by keyword
        search_results = store.search(
            query="Python generics typing",
            user_id="alice",
            tenant_id="acme",
        )
        assert len(search_results) == 1
        assert "Python 3.12" in search_results[0].content
        assert "generic" in search_results[0].content.lower()

        # Step 4: Verify stats
        assert executor.stats["total_executed"] == 1
        assert executor.stats["total_succeeded"] == 1
        assert store.count == 1
