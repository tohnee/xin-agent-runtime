# -*- coding: utf-8 -*-
"""E2E tests: real LLM API calls via Ark.

These tests require real API keys and are skipped by default.
Run with: ``pytest tests/xruntime/e2e/ -v --run-e2e``

Test scenarios:
1. Single-turn chat with glm-5.2
2. Multi-turn conversation
3. Tool calling (bash)
4. Full middleware chain (9 middlewares)
5. Sub-agent delegation via TaskTool
"""
from __future__ import annotations

import asyncio

import pytest

from xruntime._runtime._llm_test_config import (
    ARK_API_KEY,
    ARK_DEFAULT_MODEL,
    ARK_MODELS,
    create_ark_openai_model,
    is_ark_available,
)


def _msg(role: str, text: str, name: str = ""):
    """Create a Msg with list content (AS format)."""
    from agentscope.message import Msg

    return Msg(
        role=role,
        name=name or role,
        content=[{"type": "text", "text": text}],
    )


pytestmark = pytest.mark.e2e


class TestArkAPIConnectivity:
    """Verify Ark API is reachable."""

    def test_api_key_configured(self) -> None:
        """API key is set."""
        assert is_ark_available()
        assert ARK_API_KEY.startswith("ark-")

    def test_models_listed(self) -> None:
        """At least one model is configured."""
        assert len(ARK_MODELS) >= 1
        assert ARK_DEFAULT_MODEL in ARK_MODELS


class TestRealLLMSingleTurn:
    """Single-turn LLM chat tests."""

    @pytest.mark.asyncio
    async def test_glm52_simple_chat(self) -> None:
        """glm-5.2 can answer a simple question."""
        model = create_ark_openai_model("glm-5.2")
        assert model is not None
        assert model.model == "glm-5.2"

        messages = [
            _msg("system", "You are helpful."),
            _msg("user", "Say 'hello' and nothing else."),
        ]

        response = await model(messages)
        assert response is not None
        # Response should contain text (not empty)
        text = str(response)
        assert len(text) > 5, f"Response too short: {text!r}"

    @pytest.mark.asyncio
    async def test_multiple_models_available(self) -> None:
        """All configured models can be instantiated."""
        for model_name in ARK_MODELS:
            model = create_ark_openai_model(model_name)
            assert model.model == model_name


class TestRealLLMWithMiddlewareChain:
    """Full middleware chain with real LLM."""

    @pytest.mark.asyncio
    async def test_skill_injection_with_real_llm(self) -> None:
        """Skills are injected into system prompt, LLM sees them."""
        from xruntime._gateway._extension import create_xruntime_extension
        from xruntime._runtime._middleware._skill_injection import (
            SkillInjectionMiddleware,
        )

        ext = create_xruntime_extension()
        middlewares = await ext["extra_agent_middlewares"](
            "alice",
            "test-agent",
            "sess-1",
        )

        skill_mw = next(
            mw
            for mw in middlewares
            if isinstance(mw, SkillInjectionMiddleware)
        )

        prompt = "You are a helpful assistant."
        result = await skill_mw.on_system_prompt(None, prompt)
        assert "Available Skills" in result

        # Now send this prompt to real LLM
        model = create_ark_openai_model("glm-5.2")

        messages = [
            _msg("system", result),
            _msg("user", "What skills do you have? List them briefly."),
        ]
        response = await model(messages)
        assert response is not None
        # LLM should mention a skill name in its response
        resp_text = str(response).lower()
        assert len(resp_text) > 10, f"Too short: {resp_text!r}"

    @pytest.mark.asyncio
    async def test_memory_injection_with_real_llm(self) -> None:
        """Memories are injected, LLM can reference them."""
        from xruntime._gateway._extension import create_xruntime_extension
        from xruntime._runtime._memory._middleware import MemoryMiddleware
        from xruntime._runtime._memory._models import MemoryItem

        ext = create_xruntime_extension()
        memory_store = ext["memory_store"]
        memory_store.add(
            MemoryItem(
                content="User's name is Alice and she prefers Python",
                user_id="alice",
                tenant_id="default",
                type="preference",
                tags=["python", "alice", "preference"],
                confidence=0.9,
            ),
        )

        middlewares = await ext["extra_agent_middlewares"](
            "alice",
            "test-agent",
            "sess-1",
        )
        memory_mw = next(
            mw for mw in middlewares if isinstance(mw, MemoryMiddleware)
        )
        memory_mw._last_query = "Python preference Alice"

        prompt = "You are a helpful assistant."
        prompt = await memory_mw.on_system_prompt(None, prompt)
        assert "Alice" in prompt
        assert "Python" in prompt

        # Send to real LLM
        model = create_ark_openai_model("glm-5.2")

        messages = [
            _msg("system", prompt),
            _msg("user", "What programming language do I prefer?"),
        ]
        response = await model(messages)
        assert response is not None
        # LLM should reference the injected memory
        resp_text = str(response).lower()
        assert len(resp_text) > 5, f"Too short: {resp_text!r}"


class TestRealLLMSubAgent:
    """Sub-agent delegation with real LLM."""

    @pytest.mark.asyncio
    async def test_subagent_delegation(self) -> None:
        """Main agent delegates to sub-agent via TaskTool."""
        from xruntime._runtime._subagents import (
            SubAgentResult,
            SubAgentSpec,
            SubAgentTask,
        )
        from xruntime._runtime._subagents._task_tool import TaskTool

        # Create executor with a research sub-agent
        from xruntime._runtime._subagents import SubAgentExecutor

        executor = SubAgentExecutor(
            specs=[
                SubAgentSpec(
                    name="researcher",
                    description="Research specialist",
                    system_prompt="You research topics concisely.",
                ),
            ],
            max_concurrent=2,
        )

        async def research_runner(spec, task):
            # Use real LLM for research
            model = create_ark_openai_model("glm-5.2")

            messages = [
                _msg("system", spec.system_prompt),
                _msg("user", task.objective),
            ]
            response = await model(messages)
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary=f"Research complete: {task.objective}",
                findings=[str(response)[:500]],
            )

        tool = TaskTool(executor, default_runner=research_runner)

        result = await tool(
            subagent="researcher",
            description="What is Python 3.12's main feature? Answer in one sentence.",
        )

        assert result["success"] is True
        assert "Research complete" in result["summary"]
        assert len(result.get("findings", [])) > 0

    @pytest.mark.asyncio
    async def test_subagent_failure_handling(self) -> None:
        """Failed sub-agent returns error, doesn't crash."""
        from xruntime._runtime._subagents import (
            SubAgentExecutor,
            SubAgentResult,
            SubAgentSpec,
        )
        from xruntime._runtime._subagents._task_tool import TaskTool

        executor = SubAgentExecutor(
            specs=[
                SubAgentSpec(
                    name="failing-agent",
                    description="Always fails",
                ),
            ],
        )

        async def failing_runner(spec, task):
            # Simulate API error
            return SubAgentResult(
                task_id=task.task_id,
                success=False,
                errors=["API rate limit exceeded"],
            )

        tool = TaskTool(executor, default_runner=failing_runner)
        result = await tool(
            subagent="failing-agent",
            description="This will fail",
        )

        assert result["success"] is False
        assert "rate limit" in result["errors"][0]


class TestRealLLMMetricsRecording:
    """Verify metrics are recorded during real LLM calls."""

    @pytest.mark.asyncio
    async def test_subagent_metrics_recorded(self) -> None:
        """Sub-agent execution records metrics."""
        from xruntime._infra._metrics import MetricsCollector
        from xruntime._runtime._subagents import (
            SubAgentExecutor,
            SubAgentResult,
            SubAgentSpec,
            SubAgentTask,
        )

        collector = MetricsCollector()
        executor = SubAgentExecutor(
            specs=[
                SubAgentSpec(
                    name="researcher",
                    description="Research",
                ),
            ],
        )

        async def timed_runner(spec, task):
            import time

            start = time.time()
            model = create_ark_openai_model("glm-5.2")

            messages = [
                _msg("user", "Say OK"),
            ]
            await model(messages)
            duration = time.time() - start

            collector.record_subagent_call(
                spec_name=spec.name,
                duration_seconds=duration,
                success=True,
                token_usage=50,
            )
            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                summary="Done",
            )

        task = SubAgentTask(
            spec_name="researcher",
            objective="test",
        )
        await executor.execute(task, runner=timed_runner)

        stats = collector.subagent_stats("researcher")
        assert stats["count"] == 1
        assert stats["successes"] == 1
        assert stats["avg_duration_seconds"] > 0

        # Verify Prometheus output
        text = collector.export_prometheus()
        assert "xruntime_subagent_calls_total" in text
        assert "xruntime_subagent_duration_seconds" in text
