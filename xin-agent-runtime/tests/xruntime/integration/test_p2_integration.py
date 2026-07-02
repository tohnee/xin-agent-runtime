# -*- coding: utf-8 -*-
"""P2 集成测试 — 跨模块端到端场景验证.

覆盖 P2 任务的跨模块组合场景(单元测试无法捕获的集成问题):

1. Workflow SDK + Checkpoint + Resume 完整循环(run → 模拟 crash → resume)
2. Workflow SDK + FunctionExecutor 失败策略(continue / retry)端到端
3. OpenAI Adapter 端到端(parse_request → serialize_event_stream 全链路)
4. Credential Broker 完整生命周期(issue → validate → revoke → drain)
5. Credential Broker + Docker Injection 集成(无 api_key 泄漏)
6. Protocol Registry 端到端(4 个适配器全部注册 + 按路由查找)
7. Workflow Checkpoint 链(parent_checkpoint_id 跨层链接)

设计原则:
- 不依赖外部服务(无 Redis / 无 Docker / 无真实 LLM)
- 使用 InMemoryCheckpointStore / fakeredis / Mock 模式
- 每个测试聚焦跨模块协作,而非单模块内部逻辑
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from xruntime._config import CredentialBrokerConfig, XRuntimeConfig
from xruntime._gateway._adapter import AdapterRegistry
from xruntime._gateway._anthropic_adapter import AnthropicMessagesAdapter
from xruntime._gateway._claude_code_adapter import ClaudeCodeAdapter
from xruntime._gateway._extension import _default_adapters, _ROUTE_PROTOCOL_MAP
from xruntime._gateway._openai_adapter import OpenAIChatAdapter
from xruntime._gateway._opencode_adapter import OpenCodeAdapter
from xruntime._gateway._request import ProtocolType, XRuntimeRequest
from xruntime._runtime._credential import (
    BrokeredModelResolver,
    CredentialBroker,
    ShortLivedCredential,
)
from xruntime._runtime._model_resolver import (
    ModelProviderConfig,
    ModelResolver,
)
from xruntime._runtime._orchestrator import (
    Orchestrator,
    StepStatus,
    Workflow,
    WorkflowResult,
    WorkflowStatus,
    WorkflowStep,
)
from xruntime._runtime._workflow import (
    Checkpoint,
    CheckpointedOrchestrator,
    CheckpointStatus,
    InMemoryCheckpointStore,
)
from xruntime._runtime._workflow._sdk import (
    FunctionExecutor,
    WorkflowBuilder,
    load_workflow_from_file,
    resume_workflow,
    run_workflow,
)


# =====================================================================
# Part 1: Workflow SDK + Checkpoint + Resume 完整循环
# =====================================================================


class TestWorkflowRunCrashResumeCycle:
    """端到端:run → 模拟 crash → resume 完整循环."""

    @pytest.mark.asyncio
    async def test_run_persists_checkpoints_then_resume_reuses(self) -> None:
        """完整运行后 resume 应返回 COMPLETED 结果,不重新执行步骤."""
        store = InMemoryCheckpointStore()

        wf = (
            WorkflowBuilder()
            .id("wf-cycle-1")
            .step(id="s1", agent="a", prompt="p1")
            .step(id="s2", agent="a", prompt="p2", depends_on=["s1"])
            .step(id="s3", agent="a", prompt="p3", depends_on=["s2"])
            .build()
        )

        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            return f"{step.id}-ran"

        executor = FunctionExecutor(step_fn)

        # 第一次运行:完整执行
        result1 = await run_workflow(wf, executor, store=store)
        assert result1.status == WorkflowStatus.COMPLETED
        assert result1.step_results == {
            "s1": "s1-ran",
            "s2": "s2-ran",
            "s3": "s3-ran",
        }
        assert call_count == 3

        # 第二次运行:resume,不应执行任何步骤
        result2 = await resume_workflow(wf, store=store)
        assert result2 is not None
        assert result2.status == WorkflowStatus.COMPLETED
        assert result2.step_results == result1.step_results
        assert call_count == 3  # 没有新增调用

    @pytest.mark.asyncio
    async def test_resume_without_checkpoint_returns_none(self) -> None:
        """无 checkpoint 时 resume 返回 None."""
        store = InMemoryCheckpointStore()
        wf = (
            WorkflowBuilder()
            .id("wf-no-cp")
            .step(
                id="s1",
                agent="a",
                prompt="p",
            )
            .build()
        )

        result = await resume_workflow(wf, store=store)
        assert result is None

    @pytest.mark.asyncio
    async def test_checkpoint_chain_links_parent_ids(self) -> None:
        """checkpoint 链的 parent_checkpoint_id 必须正确链接."""
        store = InMemoryCheckpointStore()

        wf = (
            WorkflowBuilder()
            .id("wf-chain")
            .step(id="s1", agent="a", prompt="p1")
            .step(id="s2", agent="a", prompt="p2", depends_on=["s1"])
            .step(id="s3", agent="a", prompt="p3", depends_on=["s2"])
            .build()
        )

        executor = FunctionExecutor(lambda s, c: f"{s.id}-out")
        await run_workflow(wf, executor, store=store)

        checkpoints = await store.list_by_workflow("wf-chain")
        # 至少 3 个 layer checkpoint + 1 个 final COMPLETED
        assert len(checkpoints) >= 3

        # 第一个 checkpoint 无 parent
        assert checkpoints[0].parent_checkpoint_id is None

        # 后续 checkpoint 的 parent 必须是前一个的 checkpoint_id
        for i in range(1, len(checkpoints)):
            assert (
                checkpoints[i].parent_checkpoint_id
                == checkpoints[i - 1].checkpoint_id
            ), f"Chain broken at index {i}"

        # 最后一个必须是 COMPLETED
        assert checkpoints[-1].status == CheckpointStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_resume_after_partial_failure_with_continue(self) -> None:
        """on_failure=continue 时,resume 应跳过已完成步骤,继续后续."""
        store = InMemoryCheckpointStore()

        # 3 步线性 workflow,s2 失败但 on_failure=continue,s3 仍可运行
        wf = (
            WorkflowBuilder()
            .id("wf-partial-fail")
            .step(id="s1", agent="a", prompt="p1")
            .step(
                id="s2",
                agent="a",
                prompt="p2",
                depends_on=["s1"],
                on_failure="continue",
            )
            .step(id="s3", agent="a", prompt="p3", depends_on=["s2"])
            .build()
        )

        call_count = 0

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            if step.id == "s2":
                raise RuntimeError("s2 boom")
            return f"{step.id}-ok"

        executor = FunctionExecutor(step_fn)

        # 运行:s2 失败但 continue,s3 仍运行
        result = await run_workflow(wf, executor, store=store)
        assert result.status == WorkflowStatus.COMPLETED  # continue 不 abort
        assert result.step_status["s1"] == StepStatus.COMPLETED
        assert result.step_status["s2"] == StepStatus.FAILED
        assert result.step_status["s3"] == StepStatus.COMPLETED
        assert call_count == 3

        # resume:已完成 + 已失败的状态都应保留
        result2 = await resume_workflow(wf, store=store)
        assert result2 is not None
        assert result2.status == WorkflowStatus.COMPLETED
        assert result2.step_results["s1"] == "s1-ok"
        assert result2.step_results["s3"] == "s3-ok"
        assert call_count == 3  # 没有重新执行


# =====================================================================
# Part 2: Workflow SDK + FunctionExecutor 失败策略端到端
# =====================================================================


class TestWorkflowFailureStrategies:
    """Workflow SDK + FunctionExecutor 失败策略组合场景."""

    @pytest.mark.asyncio
    async def test_on_failure_abort_stops_workflow(self) -> None:
        """on_failure=abort 时,失败步骤之后的所有步骤被跳过."""
        wf = (
            WorkflowBuilder()
            .id("wf-abort")
            .step(id="s1", agent="a", prompt="p1")
            .step(
                id="s2",
                agent="a",
                prompt="p2",
                depends_on=["s1"],
                on_failure="abort",
            )
            .step(id="s3", agent="a", prompt="p3", depends_on=["s2"])
            .build()
        )

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "s2":
                raise ValueError("s2 failed")
            return f"{step.id}-ok"

        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.FAILED
        assert result.step_status["s1"] == StepStatus.COMPLETED
        assert result.step_status["s2"] == StepStatus.FAILED
        assert result.step_status["s3"] == StepStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_on_failure_continue_does_not_block_dependents(self) -> None:
        """on_failure=continue 时,依赖失败步骤的后续步骤仍可运行."""
        wf = (
            WorkflowBuilder()
            .id("wf-continue")
            .step(id="s1", agent="a", prompt="p1")
            .step(
                id="s2",
                agent="a",
                prompt="p2",
                depends_on=["s1"],
                on_failure="continue",
            )
            .step(id="s3", agent="a", prompt="p3", depends_on=["s2"])
            .build()
        )

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            if step.id == "s2":
                raise ValueError("s2 failed")
            return f"{step.id}-ok"

        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_status["s1"] == StepStatus.COMPLETED
        assert result.step_status["s2"] == StepStatus.FAILED
        assert result.step_status["s3"] == StepStatus.COMPLETED
        assert result.step_results["s3"] == "s3-ok"

    @pytest.mark.asyncio
    async def test_parallel_layer_all_succeed(self) -> None:
        """并行层中的所有步骤都成功完成."""
        wf = (
            WorkflowBuilder()
            .id("wf-parallel")
            .step(id="s1", agent="a", prompt="p1")
            .step(id="s2a", agent="a", prompt="p2a", depends_on=["s1"])
            .step(id="s2b", agent="a", prompt="p2b", depends_on=["s1"])
            .step(id="s3", agent="a", prompt="p3", depends_on=["s2a", "s2b"])
            .build()
        )

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            deps = ",".join(ctx.get(d, "") for d in step.depends_on)
            return f"{step.id}:{deps}"

        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["s1"] == "s1:"
        assert result.step_results["s2a"] == "s2a:s1:"
        assert result.step_results["s2b"] == "s2b:s1:"
        # s3 依赖 [s2a, s2b],step_fn 用 "," 拼接各依赖输出
        assert result.step_results["s3"] == "s3:s2a:s1:,s2b:s1:"

    @pytest.mark.asyncio
    async def test_function_executor_swallows_exceptions(self) -> None:
        """FunctionExecutor 捕获异常并返回 None(被 Orchestrator 视为失败)."""

        def boom_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            raise RuntimeError("boom")

        executor = FunctionExecutor(boom_fn)
        # 直接调用 __call__,验证异常被吞掉
        step = WorkflowStep(id="x", name="x", agent="a", prompt="p")
        result = await executor(step, {})
        assert result is None


# =====================================================================
# Part 3: OpenAI Adapter 端到端
# =====================================================================


class TestOpenAIAdapterEndToEnd:
    """OpenAI Chat Adapter parse_request → serialize_event_stream 全链路."""

    @pytest.mark.asyncio
    async def test_parse_simple_chat_request(self) -> None:
        """简单 chat 请求解析为 XRuntimeRequest."""
        adapter = OpenAIChatAdapter()
        raw = {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello!"},
            ],
        }

        req = await adapter.parse_request(raw)

        assert req.protocol == ProtocolType.OPENAI
        assert req.prompt == "Hello!"
        assert req.system_prompt == "You are helpful."
        assert req.metadata["model"] == "gpt-4"
        assert req.metadata["max_tokens"] == 4096  # default

    @pytest.mark.asyncio
    async def test_parse_with_tools_pass_through(self) -> None:
        """tools 数组原样透传(OpenAI schema == AS schema)."""
        adapter = OpenAIChatAdapter()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string"},
                        },
                        "required": ["city"],
                    },
                },
            },
        ]
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "weather?"}],
            "tools": tools,
        }

        req = await adapter.parse_request(raw)

        assert req.metadata["tools"] == tools
        # OpenAI adapter 不填充 allowed_tools(只存 metadata["tools"])
        # tools 原样透传,schema 不做转换

    @pytest.mark.asyncio
    async def test_parse_with_headers(self) -> None:
        """headers 中的 x-session-id / x-tenant-id / x-user-id 被提取."""
        adapter = OpenAIChatAdapter()
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hi"}],
        }
        headers = {
            "x-session-id": "sess-123",
            "x-tenant-id": "tenant-abc",
            "x-user-id": "user-xyz",
        }

        req = await adapter.parse_request(raw, headers=headers)

        assert req.session_id == "sess-123"
        assert req.tenant_id == "tenant-abc"
        assert req.user_id == "user-xyz"

    @pytest.mark.asyncio
    async def test_serialize_text_only_stream(self) -> None:
        """纯文本 reply 的 SSE 序列化."""
        adapter = OpenAIChatAdapter()

        async def fake_events() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "REPLY_START", "reply_id": "r1"}
            yield {"type": "TEXT_BLOCK_DELTA", "delta": "Hello"}
            yield {"type": "TEXT_BLOCK_DELTA", "delta": " world"}
            yield {"type": "REPLY_END"}

        chunks: list[bytes] = []
        async for chunk in adapter.serialize_event_stream(fake_events()):
            chunks.append(chunk)

        # 至少:1 个 role chunk + 2 个 content chunk + 1 个 finish chunk + [DONE]
        assert len(chunks) >= 4

        # 最后一个必须是 data: [DONE]\n\n
        assert chunks[-1] == b"data: [DONE]\n\n"

        # 每个 chunk 都是 data: {json}\n\n 格式
        for chunk in chunks[:-1]:
            assert chunk.startswith(b"data: ")
            assert chunk.endswith(b"\n\n")
            json_str = chunk[6:-2]
            parsed = json.loads(json_str)
            assert parsed["object"] == "chat.completion.chunk"
            assert "choices" in parsed
            assert len(parsed["choices"]) == 1

    @pytest.mark.asyncio
    async def test_serialize_tool_call_stream(self) -> None:
        """带 tool_call 的事件流序列化."""
        adapter = OpenAIChatAdapter()

        async def fake_events() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "REPLY_START", "reply_id": "r2"}
            yield {
                "type": "TOOL_CALL_START",
                "tool_call_id": "tc1",
                "tool_call_name": "get_weather",
            }
            yield {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": "tc1",
                "delta": '{"city":',
            }
            yield {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": "tc1",
                "delta": '"SF"}',
            }
            yield {"type": "REPLY_END"}

        chunks: list[bytes] = []
        async for chunk in adapter.serialize_event_stream(fake_events()):
            chunks.append(chunk)

        # 至少:role chunk + tool_call start + 2 args + finish + [DONE]
        assert len(chunks) >= 5
        assert chunks[-1] == b"data: [DONE]\n\n"

        # 找到 tool_call chunk 验证结构
        tool_chunks = []
        for chunk in chunks[:-1]:
            json_str = chunk[6:-2]
            parsed = json.loads(json_str)
            delta = parsed["choices"][0]["delta"]
            if "tool_calls" in delta:
                tool_chunks.append(delta["tool_calls"])

        assert len(tool_chunks) >= 1
        # 第一个 tool_call 应该有 function.name
        first_tc = tool_chunks[0][0]
        assert "function" in first_tc
        assert first_tc["function"]["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_thinking_blocks_are_skipped(self) -> None:
        """THINKING_BLOCK_* 事件被跳过(OpenAI 无 thinking blocks)."""
        adapter = OpenAIChatAdapter()

        async def fake_events() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "REPLY_START", "reply_id": "r3"}
            yield {"type": "TEXT_BLOCK_DELTA", "delta": "answer"}
            yield {"type": "THINKING_BLOCK_START"}
            yield {"type": "THINKING_BLOCK_DELTA", "delta": "thinking..."}
            yield {"type": "THINKING_BLOCK_END"}
            yield {"type": "REPLY_END"}

        chunks: list[bytes] = []
        async for chunk in adapter.serialize_event_stream(fake_events()):
            chunks.append(chunk)

        # 解析所有 chunk,验证没有 thinking 相关内容
        for chunk in chunks[:-1]:
            json_str = chunk[6:-2]
            parsed = json.loads(json_str)
            delta = parsed["choices"][0]["delta"]
            # delta 应该只有 role 或 content,没有 thinking
            assert "thinking" not in delta
            assert "reasoning" not in delta

        # 应该有 1 个 content chunk("answer")
        content_chunks = []
        for chunk in chunks[:-1]:
            json_str = chunk[6:-2]
            parsed = json.loads(json_str)
            delta = parsed["choices"][0]["delta"]
            if "content" in delta and delta["content"]:
                content_chunks.append(delta["content"])
        assert content_chunks == ["answer"]


# =====================================================================
# Part 4: Credential Broker 完整生命周期
# =====================================================================


class TestCredentialBrokerLifecycle:
    """CredentialBroker issue → validate → revoke → drain 全链路."""

    def _make_provider(self) -> ModelProviderConfig:
        return ModelProviderConfig(
            name="openai",
            api_key="sk-real-secret-key",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
        )

    def test_issue_validate_revoke_drain_cycle(self) -> None:
        """完整生命周期:签发 → 验证 → 撤销 → 失效队列."""
        broker = CredentialBroker()
        provider = self._make_provider()

        # 1. 签发
        cred = broker.issue(
            provider=provider,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            scopes=["chat", "embed"],
            audience="sandbox-1",
        )
        assert cred.credential_id.startswith("slc-")
        assert not cred.is_expired()
        assert cred.has_scope("chat")
        assert cred.matches_audience("sandbox-1")

        # 2. 验证(有效)
        result = broker.validate(
            cred.credential_id,
            expected_audience="sandbox-1",
            required_scopes=["chat"],
        )
        assert result.is_valid
        assert result.credential is not None

        # 3. 撤销
        broker.revoke(cred.credential_id)

        # 4. 验证(已撤销)
        result2 = broker.validate(cred.credential_id)
        assert not result2.is_valid
        assert "revoked" in result2.reason.lower()

        # 5. 失效队列
        invalidations = broker.drain_invalidations()
        assert cred.credential_id in invalidations
        # 二次 drain 应该为空(已消费)
        assert broker.drain_invalidations() == set()

    def test_session_credential_reused_within_same_turn(self) -> None:
        """同一 (tenant, session, request) 的凭证应被复用."""
        broker = CredentialBroker()
        provider = self._make_provider()

        cred1 = broker.issue_for_session(
            provider=provider,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )
        cred2 = broker.issue_for_session(
            provider=provider,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )
        assert cred1.credential_id == cred2.credential_id

        # 不同 request 应该签发新凭证
        cred3 = broker.issue_for_session(
            provider=provider,
            tenant_id="t1",
            session_id="s1",
            request_id="r2",
        )
        assert cred3.credential_id != cred1.credential_id

    def test_expired_credential_validation_fails(self) -> None:
        """过期凭证验证失败."""
        broker = CredentialBroker(
            CredentialBrokerConfig(default_ttl_seconds=1),
        )
        provider = self._make_provider()

        cred = broker.issue(
            provider=provider,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )

        # 模拟过期:直接修改 expires_at
        cred.expires_at = time.time() - 1.0
        # 重新存入 cache
        broker._cache[cred.credential_id] = cred

        result = broker.validate(cred.credential_id)
        assert not result.is_valid
        assert "expired" in result.reason.lower()

    def test_audience_mismatch_fails_closed(self) -> None:
        """audience 不匹配时验证失败(fail-closed)."""
        broker = CredentialBroker()
        provider = self._make_provider()

        cred = broker.issue(
            provider=provider,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            audience="sandbox-A",
        )

        result = broker.validate(
            cred.credential_id,
            expected_audience="sandbox-B",
        )
        assert not result.is_valid
        assert "audience" in result.reason.lower()

    def test_missing_scope_fails_validation(self) -> None:
        """缺少所需 scope 时验证失败."""
        broker = CredentialBroker()
        provider = self._make_provider()

        cred = broker.issue(
            provider=provider,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            scopes=["chat"],  # 只有 chat,没有 embed
        )

        result = broker.validate(
            cred.credential_id,
            required_scopes=["chat", "embed"],
        )
        assert not result.is_valid
        assert "scope" in result.reason.lower()
        assert "embed" in result.reason


# =====================================================================
# Part 5: Credential Broker + Docker Injection 集成
# =====================================================================


class TestCredentialBrokerDockerInjection:
    """凭证签发 → 注入字典(无 api_key 泄漏)端到端."""

    def test_injection_dict_excludes_api_key(self) -> None:
        """to_injection_dict 不得包含 api_key 字段."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-super-secret",
            model="gpt-4",
        )

        cred = broker.issue(
            provider=provider,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            scopes=["chat"],
            audience="sandbox-1",
        )

        injection = cred.to_injection_dict()

        # 关键安全断言:api_key 不得出现在注入字典中
        assert "api_key" not in injection
        assert "secret" not in str(injection).lower()
        assert "sk-super-secret" not in str(injection)
        assert "sk-super-secret" not in json.dumps(injection)

        # 但元数据应该完整
        assert injection["credential_id"] == cred.credential_id
        assert injection["provider_name"] == "openai"
        assert injection["model"] == "gpt-4"
        assert injection["scopes"] == ["chat"]
        assert injection["audience"] == "sandbox-1"
        assert "issued_at" in injection
        assert "expires_at" in injection

    def test_to_provider_config_round_trip(self) -> None:
        """to_provider_config 应该还原完整的 ModelProviderConfig(含 api_key)."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="anthropic",
            api_key="sk-ant-secret",
            model="claude-3",
            base_url="https://api.anthropic.com",
        )

        cred = broker.issue(
            provider=provider,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )

        # 注入字典(无密钥)
        injection = cred.to_injection_dict()
        assert "api_key" not in injection

        # 还原 provider config(含密钥,仅主机侧使用)
        restored = cred.to_provider_config()
        assert restored.name == "anthropic"
        assert restored.api_key == "sk-ant-secret"
        assert restored.model == "claude-3"
        assert restored.base_url == "https://api.anthropic.com"

    @pytest.mark.asyncio
    async def test_docker_injection_writes_no_secret(self) -> None:
        """Docker 注入流程写入容器的文件不含 api_key."""
        from xruntime._runtime._credential._docker_injection import (
            BROKER_CREDENTIAL_FILE,
            inject_credential_into_workspace,
        )

        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-do-not-leak",
            model="gpt-4",
        )

        cred = broker.issue(
            provider=provider,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            audience="sandbox-1",
        )

        # Mock DockerWorkspace,捕获 _write 写入的文件内容
        written_files: dict[str, bytes] = {}

        class MockDockerWorkspace:
            async def _exec(self, cmd: str, **kwargs: Any) -> Any:
                # mkdir 命令,无需捕获
                return (0, "", "")

            async def _write(
                self,
                path: str,
                data: bytes,
                **kwargs: Any,
            ) -> None:
                written_files[path] = data

        workspace = MockDockerWorkspace()
        await inject_credential_into_workspace(workspace, cred)

        # 验证写入了文件
        assert BROKER_CREDENTIAL_FILE in written_files

        # 关键安全断言:写入的内容不得包含 api_key
        content_bytes = written_files[BROKER_CREDENTIAL_FILE]
        content_str = content_bytes.decode("utf-8")
        assert "sk-do-not-leak" not in content_str
        assert "api_key" not in content_str

        # 但元数据应该完整
        parsed = json.loads(content_str)
        assert parsed["credential_id"] == cred.credential_id
        assert parsed["provider_name"] == "openai"
        assert parsed["model"] == "gpt-4"
        assert parsed["audience"] == "sandbox-1"


# =====================================================================
# Part 6: Protocol Registry 端到端
# =====================================================================


class TestProtocolRegistryEndToEnd:
    """4 个协议适配器全部注册 + 按路由查找."""

    def test_default_adapters_registers_four_protocols(self) -> None:
        """_default_adapters() 注册 4 个适配器."""
        registry = _default_adapters()

        for proto in (
            ProtocolType.ANTHROPIC,
            ProtocolType.CLAUDE_CODE,
            ProtocolType.OPENCODE,
            ProtocolType.OPENAI,
        ):
            adapter = registry.get(proto)
            assert adapter is not None, f"Missing adapter for {proto}"
            assert adapter.protocol_type == proto

    def test_route_protocol_map_covers_four_routes(self) -> None:
        """_ROUTE_PROTOCOL_MAP 覆盖 4 条路由."""
        assert len(_ROUTE_PROTOCOL_MAP) == 4
        assert _ROUTE_PROTOCOL_MAP["/v1/messages"] == ProtocolType.ANTHROPIC
        assert (
            _ROUTE_PROTOCOL_MAP["/v1/claude-code/query"]
            == ProtocolType.CLAUDE_CODE
        )
        assert _ROUTE_PROTOCOL_MAP["/v1/opencode"] == ProtocolType.OPENCODE
        assert (
            _ROUTE_PROTOCOL_MAP["/v1/chat/completions"] == ProtocolType.OPENAI
        )

    def test_registry_lookup_by_route_finds_correct_adapter(self) -> None:
        """每条路由都能找到对应类型的适配器."""
        registry = _default_adapters()

        for route, expected_proto in _ROUTE_PROTOCOL_MAP.items():
            adapter = registry.get(expected_proto)
            assert adapter is not None
            assert adapter.protocol_type == expected_proto

    def test_each_adapter_has_distinct_protocol_type(self) -> None:
        """4 个适配器各有不同的 protocol_type."""
        registry = _default_adapters()
        adapters = [
            registry.get(ProtocolType.ANTHROPIC),
            registry.get(ProtocolType.CLAUDE_CODE),
            registry.get(ProtocolType.OPENCODE),
            registry.get(ProtocolType.OPENAI),
        ]
        types = {a.protocol_type for a in adapters}
        assert len(types) == 4

    def test_registry_get_unknown_returns_none(self) -> None:
        """未知 protocol_type 返回 None(而非抛异常)."""
        registry = AdapterRegistry()
        # 使用一个未注册的伪 protocol_type
        # 由于 ProtocolType 是枚举,我们用一个未注册的真实值
        result = registry.get(ProtocolType.OPENAI)
        assert result is None


# =====================================================================
# Part 7: Workflow + Credential Broker 跨模块场景
# =====================================================================


class TestWorkflowCredentialBrokerCrossModule:
    """Workflow 步骤中使用 CredentialBroker 签发凭证的端到端场景."""

    @pytest.mark.asyncio
    async def test_workflow_step_uses_brokered_credential(self) -> None:
        """Workflow 步骤执行时通过 broker 签发短期凭证."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-workflow-secret",
            model="gpt-4",
        )

        # 步骤执行器:每个步骤签发一个凭证,并返回 credential_id
        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            cred = broker.issue(
                provider=provider,
                tenant_id="t-wf",
                session_id="s-wf",
                request_id=step.id,
                scopes=["chat"],
                audience=f"sandbox-{step.id}",
            )
            return cred.credential_id

        executor = FunctionExecutor(step_fn)

        wf = (
            WorkflowBuilder()
            .id("wf-with-creds")
            .step(id="s1", agent="a", prompt="p1")
            .step(id="s2", agent="a", prompt="p2", depends_on=["s1"])
            .build()
        )

        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results["s1"].startswith("slc-")
        assert result.step_results["s2"].startswith("slc-")
        assert result.step_results["s1"] != result.step_results["s2"]

        # 两个凭证都应该有效
        v1 = broker.validate(
            result.step_results["s1"],
            expected_audience="sandbox-s1",
        )
        v2 = broker.validate(
            result.step_results["s2"],
            expected_audience="sandbox-s2",
        )
        assert v1.is_valid
        assert v2.is_valid

    @pytest.mark.asyncio
    async def test_workflow_with_checkpointed_brokered_credentials(
        self,
    ) -> None:
        """带 checkpoint 的 workflow + broker:resume 后凭证仍可验证."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-checkpointed",
            model="gpt-4",
        )
        store = InMemoryCheckpointStore()

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            cred = broker.issue(
                provider=provider,
                tenant_id="t-cp",
                session_id="s-cp",
                request_id=step.id,
                scopes=["chat"],
            )
            return cred.credential_id

        executor = FunctionExecutor(step_fn)

        wf = (
            WorkflowBuilder()
            .id("wf-cred-cp")
            .step(id="s1", agent="a", prompt="p1")
            .step(id="s2", agent="a", prompt="p2", depends_on=["s1"])
            .build()
        )

        result1 = await run_workflow(wf, executor, store=store)
        assert result1.status == WorkflowStatus.COMPLETED

        # resume:checkpoint 中的 credential_id 仍应可验证
        result2 = await resume_workflow(wf, store=store)
        assert result2 is not None
        assert result2.status == WorkflowStatus.COMPLETED

        v1 = broker.validate(result2.step_results["s1"])
        v2 = broker.validate(result2.step_results["s2"])
        assert v1.is_valid
        assert v2.is_valid


# =====================================================================
# Part 8: OpenAI Adapter + Credential Broker 跨模块
# =====================================================================


class TestOpenAIAdapterWithCredentialBroker:
    """OpenAI 适配器请求触发凭证签发的端到端场景."""

    @pytest.mark.asyncio
    async def test_openai_request_triggers_credential_issuance(self) -> None:
        """OpenAI 请求解析后,metadata 可用于触发凭证签发."""
        adapter = OpenAIChatAdapter()
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-openai-req",
            model="gpt-4",
        )

        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
        }
        headers = {
            "x-session-id": "sess-openai-1",
            "x-tenant-id": "tenant-openai",
            "x-user-id": "user-1",
        }

        req = await adapter.parse_request(raw, headers=headers)

        # 用解析出的元数据签发凭证
        cred = broker.issue(
            provider=provider,
            tenant_id=req.tenant_id,
            session_id=req.session_id,
            request_id="req-1",
            scopes=["chat"],
            audience="sandbox-openai",
        )

        # 验证凭证有效
        result = broker.validate(
            cred.credential_id,
            expected_audience="sandbox-openai",
            required_scopes=["chat"],
        )
        assert result.is_valid

        # 验证 model 元数据被正确传递
        assert req.metadata["model"] == "gpt-4"
        assert cred.model == "gpt-4"

    @pytest.mark.asyncio
    async def test_openai_adapter_metadata_preserves_tools_for_broker(
        self,
    ) -> None:
        """OpenAI adapter 解析的 tools 元数据可用于 broker 决策."""
        adapter = OpenAIChatAdapter()
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "use tool"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "Search the web",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
        }

        req = await adapter.parse_request(raw)

        # tools 元数据应原样保留,broker 可据此决定 scopes
        tools = req.metadata["tools"]
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "search"

        # 基于工具数量决定 scopes(示例:有工具则授予 tool_use)
        scopes = ["chat", "tool_use"] if tools else ["chat"]
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-tool",
            model="gpt-4",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            scopes=scopes,
        )
        assert cred.has_scope("tool_use")


# =====================================================================
# Part 9: Workflow YAML 加载 + SDK 运行集成
# =====================================================================


class TestWorkflowYamlAndSdkIntegration:
    """YAML 加载 → SDK 运行的端到端集成."""

    @pytest.mark.asyncio
    async def test_load_yaml_and_run_via_sdk(self, tmp_path) -> None:
        """从 YAML 文件加载 workflow,然后用 SDK 运行."""
        yaml_content = """
id: wf-yaml-1
name: YAML Workflow
steps:
  - id: s1
    agent: coder
    prompt: write code
  - id: s2
    agent: reviewer
    prompt: review code
    depends_on: [s1]
"""
        yaml_file = tmp_path / "workflow.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        wf = load_workflow_from_file(yaml_file)
        assert wf.id == "wf-yaml-1"
        assert wf.name == "YAML Workflow"
        assert len(wf.steps) == 2

        def step_fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
            return f"{step.id}-done"

        executor = FunctionExecutor(step_fn)
        result = await run_workflow(wf, executor)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.step_results == {"s1": "s1-done", "s2": "s2-done"}

    @pytest.mark.asyncio
    async def test_load_yaml_and_run_with_checkpoint(self, tmp_path) -> None:
        """YAML workflow + checkpoint:运行后可 resume."""
        yaml_content = """
id: wf-yaml-cp
name: YAML Checkpointed
steps:
  - id: s1
    agent: a
    prompt: p1
  - id: s2
    agent: a
    prompt: p2
    depends_on: [s1]
"""
        yaml_file = tmp_path / "wf_cp.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        wf = load_workflow_from_file(yaml_file)
        store = InMemoryCheckpointStore()

        result1 = await run_workflow(
            wf,
            FunctionExecutor(lambda s, c: f"{s.id}-ok"),
            store=store,
        )
        assert result1.status == WorkflowStatus.COMPLETED

        result2 = await resume_workflow(wf, store=store)
        assert result2 is not None
        assert result2.status == WorkflowStatus.COMPLETED
        assert result2.step_results == result1.step_results

    def test_load_nonexistent_file_raises(self, tmp_path) -> None:
        """加载不存在的 YAML 文件应抛 FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Workflow file not found"):
            load_workflow_from_file(tmp_path / "nope.yaml")
