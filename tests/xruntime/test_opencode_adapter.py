# -*- coding: utf-8 -*-
"""Tests for the OpenCode SDK protocol adapter.

Tests cover:
- opencode.json config parsing → AS/XRuntime config
- Inbound: OpenCode request → XRuntimeRequest
- Outbound: AgentEvent dict → OpenCode event format
- Built-in tool name mapping
- Subagent/Task mapping
- End-to-end via gateway /v1/opencode
"""
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from xruntime._gateway._request import ProtocolType
from xruntime._gateway._adapter import AdapterRegistry
from xruntime._gateway._adapter import AdapterRegistry
from xruntime._gateway._opencode_adapter import (
    OpenCodeAdapter,
    parse_opencode_config,
    BUILTIN_TOOL_MAP,
)


class TestOpenCodeAdapterBasics:
    """Basic adapter protocol type."""

    def test_protocol_type(self) -> None:
        """Adapter should report OPENCODE protocol type."""
        adapter = OpenCodeAdapter()
        assert adapter.protocol_type == ProtocolType.OPENCODE

    def test_is_subclass(self) -> None:
        """Should be a ProtocolAdapter subclass."""
        from xruntime._gateway._adapter import ProtocolAdapter

        assert issubclass(OpenCodeAdapter, ProtocolAdapter)


class TestBuiltinToolMap:
    """OpenCode tool names → AS built-in tool names."""

    def test_bash_mapping(self) -> None:
        assert BUILTIN_TOOL_MAP["bash"] == "Bash"

    def test_read_mapping(self) -> None:
        assert BUILTIN_TOOL_MAP["read"] == "Read"

    def test_write_mapping(self) -> None:
        assert BUILTIN_TOOL_MAP["write"] == "Write"

    def test_edit_mapping(self) -> None:
        assert BUILTIN_TOOL_MAP["edit"] == "Edit"

    def test_glob_mapping(self) -> None:
        assert BUILTIN_TOOL_MAP["glob"] == "Glob"

    def test_grep_mapping(self) -> None:
        assert BUILTIN_TOOL_MAP["grep"] == "Grep"

    def test_task_mapping(self) -> None:
        assert BUILTIN_TOOL_MAP["task"] == "TaskCreate"


class TestParseOpenCodeConfig:
    """opencode.json config parsing."""

    def test_empty_config(self) -> None:
        """Empty config should produce empty structures."""
        cfg = parse_opencode_config({})
        assert cfg["agents"] == {}
        assert cfg["mcp_servers"] == {}
        assert cfg["skills"] == []
        assert cfg["permissions"] == {}

    def test_agents_config(self) -> None:
        """Agents should be parsed from config."""
        cfg = parse_opencode_config(
            {
                "agents": {
                    "coder": {
                        "description": "Code engineering agent",
                        "system_prompt": "You are a coder",
                        "tools": ["bash", "read", "edit"],
                    },
                },
            }
        )
        assert "coder" in cfg["agents"]
        assert cfg["agents"]["coder"]["system_prompt"] == "You are a coder"
        assert cfg["agents"]["coder"]["tools"] == ["Bash", "Read", "Edit"]

    def test_mcp_config(self) -> None:
        """MCP servers should be parsed."""
        cfg = parse_opencode_config(
            {
                "mcp": {
                    "github": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["@github/mcp"],
                    },
                },
            }
        )
        assert "github" in cfg["mcp_servers"]
        assert cfg["mcp_servers"]["github"]["command"] == "npx"

    def test_skills_config(self) -> None:
        """Skills should be parsed."""
        cfg = parse_opencode_config(
            {
                "skills": [
                    {"path": "/path/to/skills", "scan_subdir": True},
                ],
            }
        )
        assert len(cfg["skills"]) == 1
        assert cfg["skills"][0]["path"] == "/path/to/skills"

    def test_permissions_config(self) -> None:
        """Permissions should be parsed."""
        cfg = parse_opencode_config(
            {
                "permissions": {
                    "mode": "accept_edits",
                    "allow": ["Read", "Glob"],
                    "deny": ["Bash(rm *)"],
                },
            }
        )
        assert cfg["permissions"]["mode"] == "accept_edits"
        assert "Read" in cfg["permissions"]["allow"]

    def test_tool_names_mapped(self) -> None:
        """OpenCode tool names should be mapped to AS names."""
        cfg = parse_opencode_config(
            {
                "agents": {
                    "researcher": {
                        "tools": ["read", "glob", "grep", "bash"],
                    },
                },
            }
        )
        tools = cfg["agents"]["researcher"]["tools"]
        assert tools == ["Read", "Glob", "Grep", "Bash"]


class TestParseRequest:
    """Inbound: OpenCode request → XRuntimeRequest."""

    async def test_simple_prompt(self) -> None:
        """Simple prompt should parse correctly."""
        adapter = OpenCodeAdapter()
        raw = {
            "prompt": "Search for TODO comments",
            "agent": "coder",
        }
        req = await adapter.parse_request(raw)
        assert req.protocol == ProtocolType.OPENCODE
        assert req.prompt == "Search for TODO comments"
        assert req.metadata.get("agent_name") == "coder"

    async def test_with_config(self) -> None:
        """Request with inline config should merge."""
        adapter = OpenCodeAdapter()
        raw = {
            "prompt": "Run tests",
            "config": {
                "permissions": {
                    "mode": "bypass",
                },
            },
        }
        req = await adapter.parse_request(raw)
        assert req.permission_mode == "bypass"

    async def test_with_session(self) -> None:
        """session_id should be extracted."""
        adapter = OpenCodeAdapter()
        raw = {
            "prompt": "Continue",
            "session_id": "opencode-sess-1",
        }
        req = await adapter.parse_request(raw)
        assert req.session_id == "opencode-sess-1"

    async def test_with_tenant(self) -> None:
        """tenant_id from header should be used."""
        adapter = OpenCodeAdapter()
        raw = {"prompt": "Hi"}
        req = await adapter.parse_request(
            raw,
            headers={"x-tenant-id": "tenant-x"},
        )
        assert req.tenant_id == "tenant-x"


class TestSerializeEventStream:
    """Outbound: AgentEvent dict → OpenCode event format."""

    async def test_reply_start(self) -> None:
        """REPLY_START should produce an init event."""
        adapter = OpenCodeAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "REPLY_START",
                "session_id": "s1",
                "reply_id": "r1",
                "name": "agent",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        first = json.loads(chunks[0].decode())
        assert first["type"] == "session_start"
        assert first["session_id"] == "s1"

    async def test_text_delta(self) -> None:
        """TEXT_BLOCK_DELTA should produce text_delta events."""
        adapter = OpenCodeAdapter()

        async def events() -> AsyncGenerator[dict, None]:
            yield {
                "type": "REPLY_START",
                "session_id": "s1",
                "reply_id": "r1",
                "name": "agent",
            }
            yield {
                "type": "TEXT_BLOCK_DELTA",
                "reply_id": "r1",
                "block_id": "b1",
                "delta": "Hello",
            }

        chunks = []
        async for chunk in adapter.serialize_event_stream(events()):
            chunks.append(chunk)

        deltas = [
            json.loads(c.decode())
            for c in chunks
            if json.loads(c.decode())["type"] == "text_delta"
        ]
        assert len(deltas) >= 1
        assert deltas[0]["delta"] == "Hello"

    async def test_tool_call(self) -> None:
        """TOOL_CALL events should produce tool_call events."""
        adapter = OpenCodeAdapter()

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

        tool_calls = [
            json.loads(c.decode())
            for c in chunks
            if json.loads(c.decode())["type"] == "tool_call"
        ]
        assert len(tool_calls) >= 1
        assert tool_calls[0]["name"] == "Read"

    async def test_reply_end(self) -> None:
        """REPLY_END should produce session_end event."""
        adapter = OpenCodeAdapter()

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

        ends = [
            json.loads(c.decode())
            for c in chunks
            if json.loads(c.decode())["type"] == "session_end"
        ]
        assert len(ends) >= 1
