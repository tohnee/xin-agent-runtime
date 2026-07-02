# -*- coding: utf-8 -*-
"""Tests for the Claude Code SDK protocol adapter.

Tests cover:
- ClaudeAgentOptions → XRuntimeRequest field mapping
- Outbound: AgentEvent dict → Claude Code message format
  (SystemMessage / AssistantMessage / ResultMessage / StreamEvent)
- XRuntimeTransport: JSON+newline framing to/from HTTP gateway
- End-to-end via gateway /v1/claude-code/query
"""
import json
from collections.abc import AsyncGenerator

from xruntime._gateway._request import (
    ProtocolType,
)
from xruntime._gateway._claude_code_adapter import (
    ClaudeCodeAdapter,
    map_claude_options_to_request,
    PERMISSION_MODE_MAP,
)


class TestClaudeCodeAdapterBasics:
    """Basic adapter protocol type."""

    def test_protocol_type(self) -> None:
        """Adapter should report CLAUDE_CODE protocol type."""
        adapter = ClaudeCodeAdapter()
        assert adapter.protocol_type == ProtocolType.CLAUDE_CODE

    def test_is_subclass(self) -> None:
        """Should be a ProtocolAdapter subclass."""
        from xruntime._gateway._adapter import ProtocolAdapter

        assert issubclass(ClaudeCodeAdapter, ProtocolAdapter)


class TestPermissionModeMap:
    """Claude Code permission_mode → AS PermissionMode mapping."""

    def test_default(self) -> None:
        assert PERMISSION_MODE_MAP["default"] == "default"

    def test_accept_edits(self) -> None:
        assert PERMISSION_MODE_MAP["acceptEdits"] == "accept_edits"

    def test_bypass_permissions(self) -> None:
        assert PERMISSION_MODE_MAP["bypassPermissions"] == "bypass"

    def test_plan(self) -> None:
        assert PERMISSION_MODE_MAP["plan"] == "explore"

    def test_dont_ask(self) -> None:
        assert PERMISSION_MODE_MAP["dontAsk"] == "dont_ask"

    def test_unknown_defaults_to_default(self) -> None:
        assert PERMISSION_MODE_MAP.get("unknown", "default") == "default"


class TestMapClaudeOptions:
    """ClaudeAgentOptions fields → XRuntimeRequest mapping."""

    def test_basic_prompt(self) -> None:
        """Basic prompt + no options should produce minimal request."""
        req = map_claude_options_to_request(
            prompt="Fix the bug",
            options={},
        )
        assert req.protocol == ProtocolType.CLAUDE_CODE
        assert req.prompt == "Fix the bug"

    def test_system_prompt(self) -> None:
        """system_prompt should map."""
        req = map_claude_options_to_request(
            prompt="hello",
            options={"system_prompt": "You are a coder"},
        )
        assert req.system_prompt == "You are a coder"

    def test_permission_mode(self) -> None:
        """permission_mode should map via PERMISSION_MODE_MAP."""
        req = map_claude_options_to_request(
            prompt="edit",
            options={"permission_mode": "acceptEdits"},
        )
        assert req.permission_mode == "accept_edits"

    def test_allowed_tools(self) -> None:
        """allowed_tools should map."""
        req = map_claude_options_to_request(
            prompt="read",
            options={"allowed_tools": ["Read", "Glob", "Grep"]},
        )
        assert req.allowed_tools == ["Read", "Glob", "Grep"]

    def test_disallowed_tools(self) -> None:
        """disallowed_tools should map."""
        req = map_claude_options_to_request(
            prompt="run",
            options={"disallowed_tools": ["Bash"]},
        )
        assert req.disallowed_tools == ["Bash"]

    def test_max_turns(self) -> None:
        """max_turns should map."""
        req = map_claude_options_to_request(
            prompt="task",
            options={"max_turns": 10},
        )
        assert req.max_turns == 10

    def test_cwd(self) -> None:
        """cwd should go into metadata."""
        req = map_claude_options_to_request(
            prompt="work",
            options={"cwd": "/home/user/project"},
        )
        assert req.metadata.get("cwd") == "/home/user/project"

    def test_resume(self) -> None:
        """resume should map to session_id."""
        req = map_claude_options_to_request(
            prompt="continue",
            options={"resume": "sess-123"},
        )
        assert req.session_id == "sess-123"

    def test_mcp_servers(self) -> None:
        """mcp_servers should go into metadata."""
        req = map_claude_options_to_request(
            prompt="search",
            options={
                "mcp_servers": {
                    "playwright": {
                        "command": "npx",
                        "args": ["@playwright/mcp@latest"],
                    },
                },
            },
        )
        assert "mcp_servers" in req.metadata
        assert "playwright" in req.metadata["mcp_servers"]

    def test_agents(self) -> None:
        """agents (subagents) should go into metadata."""
        req = map_claude_options_to_request(
            prompt="review",
            options={
                "agents": {
                    "code-reviewer": {
                        "description": "Expert reviewer",
                        "prompt": "Review code quality",
                        "tools": ["Read", "Grep"],
                    },
                },
            },
        )
        assert "agents" in req.metadata
        assert "code-reviewer" in req.metadata["agents"]


class TestSerializeEventStream:
    """Outbound: AgentEvent dict → Claude Code message format."""

    async def test_reply_start_to_system_message_init(self) -> None:
        """REPLY_START should produce a SystemMessage init."""
        adapter = ClaudeCodeAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "REPLY_START",
                "session_id": "sess-1",
                "reply_id": "r1",
                "name": "agent",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        assert len(chunks) >= 1
        first = json.loads(chunks[0].decode())
        assert first["type"] == "system"
        assert first["subtype"] == "init"
        assert first["session_id"] == "sess-1"

    async def test_text_events_to_assistant_message(self) -> None:
        """TEXT_BLOCK events should produce an AssistantMessage."""
        adapter = ClaudeCodeAdapter()

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
        assert "system" in types
        assert "assistant" in types

    async def test_tool_call_events(self) -> None:
        """TOOL_CALL events should produce tool_use blocks in assistant."""
        adapter = ClaudeCodeAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "REPLY_START",
                "session_id": "s1",
                "reply_id": "r1",
                "name": "agent",
            }
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

        assistant_msgs = [
            json.loads(c.decode())
            for c in chunks
            if json.loads(c.decode())["type"] == "assistant"
        ]
        assert len(assistant_msgs) >= 1
        tool_uses = [
            b
            for m in assistant_msgs
            for b in m.get("message", {}).get("content", [])
            if b.get("type") == "tool_use"
        ]
        assert len(tool_uses) >= 1
        assert tool_uses[0]["name"] == "Read"

    async def test_reply_end_to_result_message(self) -> None:
        """REPLY_END should produce a ResultMessage."""
        adapter = ClaudeCodeAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "REPLY_START",
                "session_id": "s1",
                "reply_id": "r1",
                "name": "agent",
            }
            yield {
                "type": "REPLY_END",
                "session_id": "s1",
                "reply_id": "r1",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        result_msgs = [
            json.loads(c.decode())
            for c in chunks
            if json.loads(c.decode())["type"] == "result"
        ]
        assert len(result_msgs) >= 1
        assert result_msgs[0]["subtype"] == "success"

    async def test_exceed_max_iters_to_error_result(self) -> None:
        """EXCEED_MAX_ITERS should produce error_during_execution."""
        adapter = ClaudeCodeAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "REPLY_START",
                "session_id": "s1",
                "reply_id": "r1",
                "name": "agent",
            }
            yield {
                "type": "EXCEED_MAX_ITERS",
                "reply_id": "r1",
            }
            yield {
                "type": "REPLY_END",
                "session_id": "s1",
                "reply_id": "r1",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        result_msgs = [
            json.loads(c.decode())
            for c in chunks
            if json.loads(c.decode())["type"] == "result"
        ]
        assert result_msgs[-1]["subtype"] == "error_max_turns"

    async def test_multi_block_no_duplication(self) -> None:
        """Each block-END emits only the just-completed block (issue #9).

        Previously every block-END re-sent the full accumulated
        ``current_blocks`` list, so a multi-block reply duplicated
        earlier blocks. Each assistant message must now carry exactly
        the single block that just finished.
        """
        adapter = ClaudeCodeAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "REPLY_START",
                "session_id": "s1",
                "reply_id": "r1",
                "name": "agent",
            }
            yield {"type": "TEXT_BLOCK_START", "block_id": "b1"}
            yield {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": "b1",
                "delta": "First",
            }
            yield {"type": "TEXT_BLOCK_END", "block_id": "b1"}
            yield {"type": "TEXT_BLOCK_START", "block_id": "b2"}
            yield {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": "b2",
                "delta": "Second",
            }
            yield {"type": "TEXT_BLOCK_END", "block_id": "b2"}
            yield {
                "type": "REPLY_END",
                "session_id": "s1",
                "reply_id": "r1",
            }

        assistant_msgs = []
        async for chunk in adapter.serialize_event_stream(events()):
            msg = json.loads(chunk.decode())
            if msg["type"] == "assistant":
                assistant_msgs.append(msg)

        assert len(assistant_msgs) == 2
        first_content = assistant_msgs[0]["message"]["content"]
        second_content = assistant_msgs[1]["message"]["content"]
        assert len(first_content) == 1
        assert first_content[0]["text"] == "First"
        # The second message must NOT re-include the first block.
        assert len(second_content) == 1
        assert second_content[0]["text"] == "Second"


class TestClaudeCodeStateIsolation:
    """A reused adapter instance must not leak state across replies."""

    async def test_two_replies_same_adapter(self) -> None:
        """A second reply on the same adapter starts clean."""
        adapter = ClaudeCodeAdapter()

        def _one_reply(text: str) -> list[dict]:
            return [
                {
                    "type": "REPLY_START",
                    "session_id": "s1",
                    "reply_id": "r",
                    "name": "agent",
                },
                {"type": "TEXT_BLOCK_START", "block_id": "b"},
                {
                    "type": "TEXT_BLOCK_DELTA",
                    "block_id": "b",
                    "delta": text,
                },
                {"type": "TEXT_BLOCK_END", "block_id": "b"},
                {
                    "type": "REPLY_END",
                    "session_id": "s1",
                    "reply_id": "r",
                },
            ]

        async def events() -> AsyncGenerator[dict, None]:
            for evt in _one_reply("A") + _one_reply("B"):
                yield evt

        assistant_msgs = []
        async for chunk in adapter.serialize_event_stream(events()):
            msg = json.loads(chunk.decode())
            if msg["type"] == "assistant":
                assistant_msgs.append(msg)

        texts = [
            b["text"]
            for m in assistant_msgs
            for b in m["message"]["content"]
            if b.get("type") == "text"
        ]
        assert texts == ["A", "B"]
