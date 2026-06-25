# -*- coding: utf-8 -*-
"""End-to-end tests for the XRuntime protocol gateway request path.

Drives the real AgentScope stack (fakeredis-backed ``RedisStorage`` +
``RedisMessageBus`` + ``LocalWorkspaceManager`` + ``ChatService``)
through the XRuntime protocol adapters via httpx's ASGI transport,
using a ``get_model``-compatible mock model so no real API key is
needed.

Covers the P0 request-path fixes:
- credential / agent / session materialization (no 404 in ChatService),
- continuous-stream serialization (adapter cross-event state accumulates
  instead of resetting per event),
- ``chat_run_registry.spawn`` (the run is not a lost fire-and-forget
  task).
"""
import json
from typing import Any, Literal, Type

import fakeredis.aioredis
import httpx
import pytest
from asgi_lifespan import LifespanManager
from pydantic import BaseModel

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.credential import CredentialBase, CredentialFactory
from agentscope.message import TextBlock
from agentscope.model import (
    ChatModelBase,
    ChatResponse,
    StructuredResponse,
)

from xruntime._gateway._extension import (
    create_xruntime_extension,
    mount_protocol_adapters,
)
from xruntime._runtime._model_resolver import register_provider


# ---------------------------------------------------------------------------
# A get_model-compatible mock: AS's get_model builds the model via
# ``model_cls(credential=..., model=..., parameters=...)``, so the mock's
# __init__ must accept those kwargs (the tests/utils.MockModel does not).
# ---------------------------------------------------------------------------
class _MockChatCredential(CredentialBase):
    """Mock credential with a ``type`` discriminator for rehydration."""

    model_config = {"title": "Mock Chat API"}

    type: Literal["mock_chat_credential"] = "mock_chat_credential"
    api_key: str = "test"

    @classmethod
    def get_chat_model_class(cls) -> Type[ChatModelBase]:
        """Return the mock model class."""
        return _MockChatModel


class _MockChatModel(ChatModelBase):
    """Mock chat model returning a fixed non-streaming text response."""

    class Parameters(BaseModel):
        """Empty parameters."""

    def __init__(
        self,
        credential: CredentialBase,
        model: str = "mock",
        parameters: "BaseModel | None" = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters or _MockChatModel.Parameters(),
            stream=stream,
            context_size=32768,
        )

    async def _call_api(
        self,
        model_name: str,
        messages: list,
        tools: list | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Return a single fixed text response."""
        return ChatResponse(
            content=[TextBlock(text="Hello from XRuntime mock")],
            is_last=True,
        )

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list,
        structured_model: Any,
        **kwargs: Any,
    ) -> StructuredResponse:
        """Return a default structured response (for context compression)."""
        return StructuredResponse(
            content={
                "task_overview": "",
                "current_state": "",
                "important_discoveries": "",
                "next_steps": "",
                "context_to_preserve": "",
            },
        )


_MOCK_TEXT = "Hello from XRuntime mock"

# Register the mock provider once for the whole module.
CredentialFactory.register_credential(_MockChatCredential)
register_provider("mock_chat", _MockChatCredential)


def _make_storage(
    fr: fakeredis.aioredis.FakeRedis,
) -> RedisStorage:
    """Build a fakeredis-backed ``RedisStorage`` (entered by lifespan)."""

    class _S(RedisStorage):
        async def __aenter__(self) -> "RedisStorage":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _S()


def _make_bus(
    fr: fakeredis.aioredis.FakeRedis,
) -> RedisMessageBus:
    """Build a fakeredis-backed ``RedisMessageBus`` (entered by lifespan)."""

    class _B(RedisMessageBus):
        async def __aenter__(  # type: ignore[override]
            self,
        ) -> "RedisMessageBus":
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _B()


@pytest.fixture
async def app_client(monkeypatch, tmp_path):
    """Yield an httpx client against a full AS + XRuntime app.

    Sets the mock provider via env vars (read by ModelResolver at
    materialization time) and runs the AS lifespan so
    ``app.state.chat_service`` / ``chat_run_registry`` are set.
    """
    monkeypatch.setenv("XRUNTIME_MODEL_PROVIDER", "mock_chat")
    monkeypatch.setenv("XRUNTIME_MODEL_API_KEY", "test")
    monkeypatch.setenv("XRUNTIME_MODEL_NAME", "mock")

    fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage = _make_storage(fr)
    bus = _make_bus(fr)
    workspace_manager = LocalWorkspaceManager(
        basedir=str(tmp_path / "ws"),
    )

    ext = create_xruntime_extension()
    app = create_app(
        storage=storage,
        message_bus=bus,
        workspace_manager=workspace_manager,
        extra_agent_middlewares=ext["extra_agent_middlewares"],
    )
    mount_protocol_adapters(
        app,
        ext["adapter_registry"],
        config=ext["config"],
        model_resolver=ext["model_resolver"],
    )

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=httpx.Timeout(30.0),
        ) as client:
            yield client

    await fr.aclose()


async def _ndjson_lines(response: httpx.Response) -> list[dict]:
    """Collect NDJSON lines from a streaming response into dicts."""
    lines: list[dict] = []
    async for raw in response.aiter_lines():
        raw = raw.strip()
        if not raw:
            continue
        lines.append(json.loads(raw))
    return lines


class TestAnthropicMessagesEndpoint:
    """POST /v1/messages end-to-end."""

    async def test_streams_anthropic_event_sequence(
        self,
        app_client,
    ) -> None:
        """Stream message_start -> content_block_* -> message_stop."""
        body = {
            "model": "mock",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
        }
        async with app_client.stream(
            "POST",
            "/v1/messages",
            json=body,
        ) as response:
            assert response.status_code == 200
            events = await _ndjson_lines(response)

        types = [e.get("type") for e in events]
        assert "message_start" in types
        assert "content_block_start" in types
        assert "content_block_stop" in types
        # REPLY_END maps to message_delta + message_stop.
        assert "message_delta" in types
        assert "message_stop" in types

        # The mock model's text must reach the client.
        all_text = " ".join(
            str(e.get("delta", {}).get("text", ""))
            for e in events
            if e.get("type") == "content_block_delta"
        )
        assert _MOCK_TEXT in all_text


class TestClaudeCodeEndpoint:
    """POST /v1/claude-code/query end-to-end."""

    async def test_streams_system_assistant_result(
        self,
        app_client,
    ) -> None:
        """A query streams system(init) -> assistant -> result."""
        body = {"prompt": "Hi", "options": {}}
        async with app_client.stream(
            "POST",
            "/v1/claude-code/query",
            json=body,
        ) as response:
            assert response.status_code == 200
            messages = await _ndjson_lines(response)

        types = [m.get("type") for m in messages]
        assert "system" in types
        assert "assistant" in types
        assert "result" in types

        # The system message carries an init session id.
        system_msg = next(m for m in messages if m.get("type") == "system")
        assert system_msg.get("subtype") == "init"
        assert system_msg.get("session_id")

        # The assistant message carries the mock text in its content.
        assistant = next(m for m in messages if m.get("type") == "assistant")
        content = assistant.get("message", {}).get("content", [])
        text_blocks = [b for b in content if b.get("type") == "text"]
        assert text_blocks, "assistant message has no text block"
        assert any(_MOCK_TEXT in b.get("text", "") for b in text_blocks)

        # The result message reports success.
        result = next(m for m in messages if m.get("type") == "result")
        assert result.get("subtype") == "success"
