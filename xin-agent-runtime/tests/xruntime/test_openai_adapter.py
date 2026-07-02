# -*- coding: utf-8 -*-
"""TDD tests for the OpenAI Chat Completions protocol adapter.

Covers:

1. :class:`OpenAIChatAdapter` — protocol type + subclass contract.
2. ``parse_request`` — converts OpenAI Chat Completions body
   (``{"model", "messages", "tools", "max_tokens", "temperature"}``)
   into :class:`XRuntimeRequest`.
3. ``serialize_event_stream`` — converts an ``AgentEvent`` dict
   stream into OpenAI SSE-formatted bytes
   (``data: {...}\\n\\n`` chunks terminated by ``data: [DONE]\\n\\n``).
4. Tool schema conversion (no-op for OpenAI since it already uses
   the OpenAI function-calling schema).
5. Route + registry wiring — ``/v1/chat/completions`` is mapped to
   ``ProtocolType.OPENAI`` and the adapter is registered in
   :func:`_default_adapters`.
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from xruntime._gateway._adapter import ProtocolAdapter
from xruntime._gateway._request import ProtocolType, XRuntimeRequest


# ── 1. Adapter basics ───────────────────────────────────────────────


class TestOpenAIAdapterBasics:
    """OpenAIChatAdapter — protocol type + subclass contract."""

    def test_protocol_type_is_openai(self) -> None:
        """Adapter should report OPENAI protocol type."""
        from xruntime._gateway._openai_adapter import OpenAIChatAdapter

        adapter = OpenAIChatAdapter()
        assert adapter.protocol_type == ProtocolType.OPENAI

    def test_is_subclass_of_protocol_adapter(self) -> None:
        """Should be a ProtocolAdapter subclass."""
        from xruntime._gateway._openai_adapter import OpenAIChatAdapter

        assert issubclass(OpenAIChatAdapter, ProtocolAdapter)

    def test_openai_protocol_type_value(self) -> None:
        """ProtocolType.OPENAI should serialize to 'openai'."""
        assert ProtocolType.OPENAI.value == "openai"


# ── 2. parse_request ────────────────────────────────────────────────


class TestParseRequest:
    """Inbound: OpenAI Chat Completions body → XRuntimeRequest."""

    @pytest.fixture
    def adapter(self) -> Any:
        from xruntime._gateway._openai_adapter import OpenAIChatAdapter

        return OpenAIChatAdapter()

    async def test_simple_text_request(self, adapter: Any) -> None:
        """A simple text-only request should parse correctly."""
        raw = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": "Hello!"},
            ],
            "max_tokens": 1024,
        }
        req = await adapter.parse_request(raw)
        assert req.protocol == ProtocolType.OPENAI
        assert req.prompt == "Hello!"
        assert req.session_id is None
        assert req.system_prompt is None

    async def test_request_with_system_prompt(self, adapter: Any) -> None:
        """System message should be extracted into system_prompt."""
        raw = {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ],
        }
        req = await adapter.parse_request(raw)
        assert req.system_prompt == "You are helpful."
        assert req.prompt == "Hi"

    async def test_uses_last_user_message_as_prompt(
        self,
        adapter: Any,
    ) -> None:
        """When multiple user messages, the last one becomes the prompt."""
        raw = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "second"},
            ],
        }
        req = await adapter.parse_request(raw)
        assert req.prompt == "second"

    async def test_request_with_session_header(self, adapter: Any) -> None:
        """Session id should come from x-session-id header."""
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        req = await adapter.parse_request(
            raw,
            headers={"x-session-id": "sess-xyz"},
        )
        assert req.session_id == "sess-xyz"

    async def test_request_with_tenant_header(self, adapter: Any) -> None:
        """Tenant id should come from x-tenant-id header."""
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        req = await adapter.parse_request(
            raw,
            headers={"x-tenant-id": "acme"},
        )
        assert req.tenant_id == "acme"

    async def test_request_with_user_header(self, adapter: Any) -> None:
        """User id should come from x-user-id header."""
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        req = await adapter.parse_request(
            raw,
            headers={"x-user-id": "alice"},
        )
        assert req.user_id == "alice"

    async def test_request_with_tools(self, adapter: Any) -> None:
        """OpenAI tools should be passed through (already OpenAI schema)."""
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "weather?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string"},
                            },
                            "required": ["location"],
                        },
                    },
                },
            ],
        }
        req = await adapter.parse_request(raw)
        assert req.metadata["tools"] == raw["tools"]

    async def test_metadata_includes_model(self, adapter: Any) -> None:
        """metadata should carry the requested model name."""
        raw = {
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        req = await adapter.parse_request(raw)
        assert req.metadata["model"] == "gpt-4-turbo"

    async def test_metadata_includes_max_tokens(self, adapter: Any) -> None:
        """metadata should carry max_tokens (default 4096)."""
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 2048,
        }
        req = await adapter.parse_request(raw)
        assert req.metadata["max_tokens"] == 2048

    async def test_metadata_default_max_tokens(self, adapter: Any) -> None:
        """When max_tokens missing, default 4096 is used."""
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        req = await adapter.parse_request(raw)
        assert req.metadata["max_tokens"] == 4096

    async def test_metadata_includes_all_messages(
        self,
        adapter: Any,
    ) -> None:
        """Full messages list should be stored for context reconstruction."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        raw = {"model": "gpt-4", "messages": messages}
        req = await adapter.parse_request(raw)
        assert req.metadata["all_messages"] == messages

    async def test_metadata_includes_temperature(self, adapter: Any) -> None:
        """temperature should be carried into metadata when present."""
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
            "temperature": 0.5,
        }
        req = await adapter.parse_request(raw)
        assert req.metadata["temperature"] == 0.5

    async def test_tool_choice_passed_through(self, adapter: Any) -> None:
        """tool_choice should be carried into metadata when present."""
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
            "tool_choice": "auto",
        }
        req = await adapter.parse_request(raw)
        assert req.metadata["tool_choice"] == "auto"

    async def test_content_blocks_extracted_from_user_message(
        self,
        adapter: Any,
    ) -> None:
        """User content may be a list of text blocks; should be flattened."""
        raw = {
            "model": "gpt-4",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hello "},
                        {"type": "text", "text": "world"},
                    ],
                },
            ],
        }
        req = await adapter.parse_request(raw)
        assert req.prompt == "Hello world"

    async def test_empty_messages_yields_empty_prompt(
        self,
        adapter: Any,
    ) -> None:
        """An empty messages list should yield an empty prompt."""
        raw = {"model": "gpt-4", "messages": []}
        req = await adapter.parse_request(raw)
        assert req.prompt == ""

    async def test_non_string_non_list_content_returns_empty(
        self,
        adapter: Any,
    ) -> None:
        """Non-str / non-list content (e.g. None) yields empty text."""
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": None}],
        }
        req = await adapter.parse_request(raw)
        assert req.prompt == ""

    async def test_tools_explicitly_none_treated_as_empty(
        self,
        adapter: Any,
    ) -> None:
        """``tools: null`` should be treated as no tools."""
        raw = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": None,
        }
        req = await adapter.parse_request(raw)
        assert "tools" not in req.metadata


# ── 3. serialize_event_stream ───────────────────────────────────────


async def _collect_stream(
    stream: AsyncGenerator[bytes, None],
) -> list[bytes]:
    """Collect an async byte stream into a list."""
    chunks: list[bytes] = []
    async for chunk in stream:
        chunks.append(chunk)
    return chunks


def _parse_sse_chunks(chunks: list[bytes]) -> list[dict[str, Any] | str]:
    """Parse ``data: {...}\\n\\n`` chunks into JSON objects / [DONE]."""
    out: list[dict[str, Any] | str] = []
    for chunk in chunks:
        text = chunk.decode()
        # Each chunk may contain one or more ``data:`` lines
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            payload = line[len("data: ") :]
            if payload == "[DONE]":
                out.append("[DONE]")
            else:
                out.append(json.loads(payload))
    return out


class TestSerializeEventStream:
    """Outbound: AgentEvent dict stream → OpenAI SSE bytes."""

    @pytest.fixture
    def adapter(self) -> Any:
        from xruntime._gateway._openai_adapter import OpenAIChatAdapter

        return OpenAIChatAdapter()

    async def _run_serializer(
        self,
        adapter: Any,
        events: list[dict[str, Any]],
    ) -> list[bytes]:
        """Run the adapter's serializer over a list of events."""

        async def _gen() -> AsyncGenerator[dict[str, Any], None]:
            for evt in events:
                yield evt

        return await _collect_stream(adapter.serialize_event_stream(_gen()))

    async def test_empty_stream_yields_nothing(
        self,
        adapter: Any,
    ) -> None:
        """An empty event stream should produce no chunks."""
        chunks = await self._run_serializer(adapter, [])
        assert chunks == []

    async def test_reply_start_emits_first_chunk(
        self,
        adapter: Any,
    ) -> None:
        """REPLY_START should emit the first chunk with role=assistant."""
        chunks = await self._run_serializer(
            adapter,
            [{"type": "REPLY_START", "reply_id": "chatcmpl-abc"}],
        )
        parsed = _parse_sse_chunks(chunks)
        assert len(parsed) == 1
        data = parsed[0]
        assert data["object"] == "chat.completion.chunk"
        assert data["id"] == "chatcmpl-abc"
        choices = data["choices"]
        assert len(choices) == 1
        assert choices[0]["delta"]["role"] == "assistant"
        assert choices[0]["finish_reason"] is None

    async def test_text_delta_emits_content_chunk(
        self,
        adapter: Any,
    ) -> None:
        """TEXT_BLOCK_DELTA should emit a chunk with delta.content."""
        events = [
            {"type": "REPLY_START", "reply_id": "r1"},
            {"type": "TEXT_BLOCK_DELTA", "delta": "Hello"},
            {"type": "TEXT_BLOCK_DELTA", "delta": " world"},
        ]
        chunks = await self._run_serializer(adapter, events)
        parsed = _parse_sse_chunks(chunks)
        # 1 (REPLY_START) + 2 (TEXT_BLOCK_DELTA)
        assert len(parsed) == 3
        # The second chunk should contain "Hello"
        assert parsed[1]["choices"][0]["delta"]["content"] == "Hello"
        assert parsed[2]["choices"][0]["delta"]["content"] == " world"

    async def test_reply_end_emits_finish_reason_and_done(
        self,
        adapter: Any,
    ) -> None:
        """REPLY_END should emit a chunk with finish_reason + [DONE]."""
        events = [
            {"type": "REPLY_START", "reply_id": "r1"},
            {"type": "REPLY_END"},
        ]
        chunks = await self._run_serializer(adapter, events)
        parsed = _parse_sse_chunks(chunks)
        # 1 (REPLY_START) + 1 (REPLY_END with finish_reason=stop) + [DONE]
        assert len(parsed) == 3
        assert parsed[1]["choices"][0]["finish_reason"] == "stop"
        assert parsed[1]["choices"][0]["delta"] == {}
        assert parsed[2] == "[DONE]"

    async def test_tool_use_yields_tool_calls_finish_reason(
        self,
        adapter: Any,
    ) -> None:
        """Tool calls should produce finish_reason='tool_calls'."""
        events = [
            {"type": "REPLY_START", "reply_id": "r1"},
            {
                "type": "TOOL_CALL_START",
                "tool_call_id": "call_1",
                "tool_call_name": "get_weather",
            },
            {"type": "TOOL_CALL_DELTA", "delta": '{"location":"NYC"}'},
            {"type": "TOOL_CALL_END", "tool_call_id": "call_1"},
            {"type": "REPLY_END"},
        ]
        chunks = await self._run_serializer(adapter, events)
        parsed = _parse_sse_chunks(chunks)
        # The final REPLY_END chunk should have finish_reason=tool_calls
        finish_chunk = parsed[-2]  # last before [DONE]
        assert finish_chunk["choices"][0]["finish_reason"] == "tool_calls"
        assert parsed[-1] == "[DONE]"

    async def test_tool_call_start_emits_tool_call_delta(
        self,
        adapter: Any,
    ) -> None:
        """TOOL_CALL_START should emit a chunk with tool_calls delta."""
        events = [
            {"type": "REPLY_START", "reply_id": "r1"},
            {
                "type": "TOOL_CALL_START",
                "tool_call_id": "call_1",
                "tool_call_name": "get_weather",
            },
        ]
        chunks = await self._run_serializer(adapter, events)
        parsed = _parse_sse_chunks(chunks)
        # The TOOL_CALL_START chunk
        tool_chunk = parsed[-1]
        tool_calls = tool_chunk["choices"][0]["delta"]["tool_calls"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "call_1"
        assert tool_calls[0]["function"]["name"] == "get_weather"

    async def test_thinking_block_delta_is_skipped(
        self,
        adapter: Any,
    ) -> None:
        """THINKING_BLOCK_DELTA should not produce any SSE chunks."""
        events = [
            {"type": "REPLY_START", "reply_id": "r1"},
            {"type": "THINKING_BLOCK_DELTA", "delta": "thinking..."},
            {"type": "REPLY_END"},
        ]
        chunks = await self._run_serializer(adapter, events)
        parsed = _parse_sse_chunks(chunks)
        # Only REPLY_START + REPLY_END + [DONE]
        assert len(parsed) == 3

    async def test_chunks_are_sse_formatted(
        self,
        adapter: Any,
    ) -> None:
        """Each chunk should be ``data: <json>\\n\\n`` formatted."""
        events = [{"type": "REPLY_START", "reply_id": "r1"}]
        chunks = await self._run_serializer(adapter, events)
        text = chunks[0].decode()
        assert text.startswith("data: ")
        assert text.endswith("\n\n")

    async def test_done_terminator_is_emitted(self, adapter: Any) -> None:
        """``data: [DONE]\\n\\n`` should always be emitted at the end."""
        events = [
            {"type": "REPLY_START", "reply_id": "r1"},
            {"type": "REPLY_END"},
        ]
        chunks = await self._run_serializer(adapter, events)
        last = chunks[-1].decode()
        assert last == "data: [DONE]\n\n"

    async def test_full_text_stream(self, adapter: Any) -> None:
        """A full text-only stream should produce the expected sequence."""
        events = [
            {"type": "REPLY_START", "reply_id": "chatcmpl-1"},
            {"type": "TEXT_BLOCK_DELTA", "delta": "Hello"},
            {"type": "TEXT_BLOCK_DELTA", "delta": "!"},
            {"type": "REPLY_END"},
        ]
        chunks = await self._run_serializer(adapter, events)
        parsed = _parse_sse_chunks(chunks)
        assert len(parsed) == 5  # start + 2 deltas + finish + DONE
        assert parsed[0]["choices"][0]["delta"]["role"] == "assistant"
        assert parsed[1]["choices"][0]["delta"]["content"] == "Hello"
        assert parsed[2]["choices"][0]["delta"]["content"] == "!"
        assert parsed[3]["choices"][0]["finish_reason"] == "stop"
        assert parsed[4] == "[DONE]"


# ── 4. Registry + route wiring ─────────────────────────────────────


class TestRegistryAndRouteWiring:
    """Adapter should be auto-registered and route should be mapped."""

    def test_openai_in_default_adapters(self) -> None:
        """_default_adapters() should include an OpenAIChatAdapter."""
        from xruntime._gateway._extension import _default_adapters

        registry = _default_adapters()
        adapter = registry.get(ProtocolType.OPENAI)
        assert adapter is not None
        from xruntime._gateway._openai_adapter import OpenAIChatAdapter

        assert isinstance(adapter, OpenAIChatAdapter)

    def test_chat_completions_route_in_protocol_map(self) -> None:
        """``/v1/chat/completions`` should map to OPENAI protocol."""
        from xruntime._gateway._extension import _ROUTE_PROTOCOL_MAP

        assert (
            _ROUTE_PROTOCOL_MAP.get("/v1/chat/completions")
            == ProtocolType.OPENAI
        )

    def test_default_adapters_has_all_four_protocols(self) -> None:
        """All 4 protocols should be registered by default."""
        from xruntime._gateway._extension import _default_adapters

        registry = _default_adapters()
        registered = set(registry.list_registered())
        assert registered == {
            ProtocolType.ANTHROPIC,
            ProtocolType.CLAUDE_CODE,
            ProtocolType.OPENCODE,
            ProtocolType.OPENAI,
        }
