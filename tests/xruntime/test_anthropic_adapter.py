# -*- coding: utf-8 -*-
"""Tests for the Anthropic Messages API protocol adapter.

Tests cover:
- Inbound: Anthropic request body → XRuntimeRequest
- Outbound: AgentEvent dict stream → Anthropic SSE event stream
- Tool schema conversion: Anthropic tool schema ↔ AS OpenAI function schema
- Session routing: stateless vs stateful
"""
import json
from collections.abc import AsyncGenerator

from xruntime._gateway._request import (
    ProtocolType,
)
from xruntime._gateway._anthropic_adapter import (
    AnthropicMessagesAdapter,
    convert_anthropic_tools_to_as,
    convert_as_tools_to_anthropic,
)


class TestAnthropicAdapterBasics:
    """Basic adapter protocol type and instantiation."""

    def test_protocol_type(self) -> None:
        """Adapter should report ANTHROPIC protocol type."""
        adapter = AnthropicMessagesAdapter()
        assert adapter.protocol_type == ProtocolType.ANTHROPIC

    def test_is_subclass_of_protocol_adapter(self) -> None:
        """Should be a ProtocolAdapter subclass."""
        from xruntime._gateway._adapter import ProtocolAdapter

        assert issubclass(AnthropicMessagesAdapter, ProtocolAdapter)


class TestParseRequest:
    """Inbound: Anthropic request body → XRuntimeRequest."""

    async def test_simple_text_request(self) -> None:
        """A simple text-only request should parse correctly."""
        adapter = AnthropicMessagesAdapter()
        raw = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "Hello, Claude!"},
            ],
            "max_tokens": 1024,
        }
        req = await adapter.parse_request(raw)
        assert req.protocol == ProtocolType.ANTHROPIC
        assert req.prompt == "Hello, Claude!"
        assert req.session_id is None

    async def test_request_with_system_prompt(self) -> None:
        """System prompt should be extracted."""
        adapter = AnthropicMessagesAdapter()
        raw = {
            "model": "claude-sonnet-4-20250514",
            "system": "You are a helpful assistant.",
            "messages": [
                {"role": "user", "content": "Hi"},
            ],
            "max_tokens": 1024,
        }
        req = await adapter.parse_request(raw)
        assert req.system_prompt == "You are a helpful assistant."

    async def test_request_with_session_header(self) -> None:
        """Session id from x-session-id header should be used."""
        adapter = AnthropicMessagesAdapter()
        raw = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "Continue"},
            ],
            "max_tokens": 1024,
        }
        req = await adapter.parse_request(
            raw,
            headers={"x-session-id": "sess-abc"},
        )
        assert req.session_id == "sess-abc"

    async def test_request_with_tenant_header(self) -> None:
        """Tenant id from x-tenant-id header should be used."""
        adapter = AnthropicMessagesAdapter()
        raw = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "Hi"},
            ],
            "max_tokens": 1024,
        }
        req = await adapter.parse_request(
            raw,
            headers={"x-tenant-id": "acme-corp"},
        )
        assert req.tenant_id == "acme-corp"

    async def test_request_with_tools(self) -> None:
        """Anthropic tools should be converted and stored in metadata."""
        adapter = AnthropicMessagesAdapter()
        raw = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "List files"},
            ],
            "tools": [
                {
                    "name": "list_files",
                    "description": "List files in a directory",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Directory path",
                            },
                        },
                        "required": ["path"],
                    },
                },
            ],
            "max_tokens": 1024,
        }
        req = await adapter.parse_request(raw)
        assert "tools" in req.metadata
        assert len(req.metadata["tools"]) == 1

    async def test_multimodal_content_text(self) -> None:
        """Content as array of blocks should extract text."""
        adapter = AnthropicMessagesAdapter()
        raw = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image"},
                    ],
                },
            ],
            "max_tokens": 1024,
        }
        req = await adapter.parse_request(raw)
        assert "Describe this image" in req.prompt

    async def test_last_user_message_is_prompt(self) -> None:
        """Prompt should be the last user message text."""
        adapter = AnthropicMessagesAdapter()
        raw = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "First message"},
                {"role": "assistant", "content": "OK"},
                {"role": "user", "content": "Second message"},
            ],
            "max_tokens": 1024,
        }
        req = await adapter.parse_request(raw)
        assert req.prompt == "Second message"


class TestToolSchemaConversion:
    """Anthropic tool schema ↔ AS OpenAI function schema."""

    def test_anthropic_to_as(self) -> None:
        """Anthropic tool schema should convert to AS format."""
        anthropic_tools = [
            {
                "name": "list_files",
                "description": "List files in a directory",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        ]
        as_tools = convert_anthropic_tools_to_as(anthropic_tools)
        assert len(as_tools) == 1
        assert as_tools[0]["type"] == "function"
        assert as_tools[0]["function"]["name"] == "list_files"
        assert (
            as_tools[0]["function"]["description"]
            == "List files in a directory"
        )
        assert "properties" in as_tools[0]["function"]["parameters"]

    def test_as_to_anthropic(self) -> None:
        """AS function schema should convert to Anthropic format."""
        as_tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                        },
                        "required": ["path"],
                    },
                },
            },
        ]
        anthropic_tools = convert_as_tools_to_anthropic(as_tools)
        assert len(anthropic_tools) == 1
        assert anthropic_tools[0]["name"] == "read_file"
        assert anthropic_tools[0]["description"] == "Read a file"
        assert "input_schema" in anthropic_tools[0]
        assert anthropic_tools[0]["input_schema"]["type"] == "object"

    def test_roundtrip(self) -> None:
        """Anthropic → AS → Anthropic should preserve name/description."""
        original = [
            {
                "name": "grep",
                "description": "Search file contents",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            },
        ]
        as_tools = convert_anthropic_tools_to_as(original)
        back = convert_as_tools_to_anthropic(as_tools)
        assert back[0]["name"] == original[0]["name"]
        assert back[0]["description"] == original[0]["description"]

    def test_empty_tools(self) -> None:
        """Empty tool list should convert to empty list."""
        assert convert_anthropic_tools_to_as([]) == []
        assert convert_as_tools_to_anthropic([]) == []


class TestSerializeEventStream:
    """Outbound: AgentEvent dict stream → Anthropic SSE bytes."""

    async def test_reply_start_to_message_start(self) -> None:
        """ReplyStart should produce message_start SSE event."""
        adapter = AnthropicMessagesAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "REPLY_START",
                "session_id": "sess-1",
                "reply_id": "reply-1",
                "name": "agent",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        assert len(chunks) >= 1
        first = json.loads(chunks[0].decode())
        assert first["type"] == "message_start"
        assert "message" in first

    async def test_text_block_events(self) -> None:
        """TextBlock Start/Delta/End should produce content_block events."""
        adapter = AnthropicMessagesAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "TEXT_BLOCK_START",
                "reply_id": "r1",
                "block_id": "b1",
            }
            yield {
                "type": "TEXT_BLOCK_DELTA",
                "reply_id": "r1",
                "block_id": "b1",
                "delta": "Hello",
            }
            yield {
                "type": "TEXT_BLOCK_END",
                "reply_id": "r1",
                "block_id": "b1",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        types = [json.loads(c.decode())["type"] for c in chunks]
        assert "content_block_start" in types
        assert "content_block_delta" in types
        assert "content_block_stop" in types

    async def test_thinking_block_events(self) -> None:
        """ThinkingBlock events should produce thinking blocks."""
        adapter = AnthropicMessagesAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "THINKING_BLOCK_START",
                "reply_id": "r1",
                "block_id": "b-think",
            }
            yield {
                "type": "THINKING_BLOCK_DELTA",
                "reply_id": "r1",
                "block_id": "b-think",
                "delta": "Hmm",
            }
            yield {
                "type": "THINKING_BLOCK_END",
                "reply_id": "r1",
                "block_id": "b-think",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        starts = [
            json.loads(c.decode())
            for c in chunks
            if json.loads(c.decode())["type"] == "content_block_start"
        ]
        assert any(s["content_block"]["type"] == "thinking" for s in starts)

    async def test_tool_call_events(self) -> None:
        """ToolCall events should produce tool_use content_block events."""
        adapter = AnthropicMessagesAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "TOOL_CALL_START",
                "reply_id": "r1",
                "tool_call_id": "tc-1",
                "tool_call_name": "Read",
            }
            yield {
                "type": "TOOL_CALL_DELTA",
                "reply_id": "r1",
                "tool_call_id": "tc-1",
                "delta": '{"path":"foo.py"}',
            }
            yield {
                "type": "TOOL_CALL_END",
                "reply_id": "r1",
                "tool_call_id": "tc-1",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        starts = [
            json.loads(c.decode())
            for c in chunks
            if json.loads(c.decode())["type"] == "content_block_start"
        ]
        assert any(s["content_block"]["type"] == "tool_use" for s in starts)

    async def test_reply_end_to_message_stop(self) -> None:
        """ReplyEnd should produce message_delta + message_stop."""
        adapter = AnthropicMessagesAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "REPLY_END",
                "session_id": "sess-1",
                "reply_id": "r1",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        types = [json.loads(c.decode())["type"] for c in chunks]
        assert "message_delta" in types
        assert "message_stop" in types

    async def test_full_flow_sequence(self) -> None:
        """A complete reply flow should produce valid SSE sequence."""
        adapter = AnthropicMessagesAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "REPLY_START",
                "session_id": "s1",
                "reply_id": "r1",
                "name": "agent",
            }
            yield {
                "type": "TEXT_BLOCK_START",
                "reply_id": "r1",
                "block_id": "b1",
            }
            yield {
                "type": "TEXT_BLOCK_DELTA",
                "reply_id": "r1",
                "block_id": "b1",
                "delta": "Hello world",
            }
            yield {
                "type": "TEXT_BLOCK_END",
                "reply_id": "r1",
                "block_id": "b1",
            }
            yield {
                "type": "REPLY_END",
                "session_id": "s1",
                "reply_id": "r1",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        assert len(chunks) >= 5
        types = [json.loads(c.decode())["type"] for c in chunks]
        assert types[0] == "message_start"
        assert types[-1] == "message_stop"


class TestAnthropicBlockIndexReset:
    """REPLY_START must reset block index across replies (issue #8)."""

    async def test_block_index_resets_per_reply(self) -> None:
        """The second reply's first block starts at index 0 again."""
        adapter = AnthropicMessagesAdapter()

        def _reply() -> list[dict]:
            return [
                {"type": "REPLY_START", "reply_id": "r"},
                {"type": "TEXT_BLOCK_START", "block_id": "b"},
                {
                    "type": "TEXT_BLOCK_DELTA",
                    "block_id": "b",
                    "delta": "hi",
                },
                {"type": "TEXT_BLOCK_END", "block_id": "b"},
                {
                    "type": "REPLY_END",
                    "session_id": "s",
                    "reply_id": "r",
                },
            ]

        async def events() -> AsyncGenerator[dict, None]:
            for evt in _reply() + _reply():
                yield evt

        start_indices = []
        async for chunk in adapter.serialize_event_stream(events()):
            msg = json.loads(chunk.decode())
            if msg.get("type") == "content_block_start":
                start_indices.append(msg["index"])

        # Two replies, each with exactly one content block at index 0.
        assert start_indices == [0, 0]
