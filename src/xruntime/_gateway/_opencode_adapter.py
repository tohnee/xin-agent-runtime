# -*- coding: utf-8 -*-
"""OpenCode SDK protocol adapter.

Converts between the OpenCode SDK wire format and XRuntime's internal
:class:`XRuntimeRequest` / event stream.

OpenCode uses a JSON-based protocol with ``opencode.json`` for
declarative configuration (agents, skills, MCP, permissions, plugins).

Inbound (OpenCode → XRuntime):
    - ``prompt``: → ``prompt``
    - ``agent``: → ``metadata["agent_name"]`` (selects agent blueprint)
    - ``config``: inline ``opencode.json`` fragment, parsed via
      :func:`parse_opencode_config` and merged into metadata.
    - ``session_id``: → ``session_id``

Outbound (AgentEvent → OpenCode events):
    - ``REPLY_START`` → ``session_start``
    - ``TEXT_BLOCK_DELTA`` → ``text_delta``
    - ``TOOL_CALL_*`` → ``tool_call``
    - ``REPLY_END`` → ``session_end``
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from ._adapter import ProtocolAdapter
from ._request import ProtocolType, XRuntimeRequest


BUILTIN_TOOL_MAP: dict[str, str] = {
    "bash": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "glob": "Glob",
    "grep": "Grep",
    "task": "TaskCreate",
}
"""Maps OpenCode lowercase tool names to AS built-in tool class names."""


def _map_tool_names(
    tools: list[str],
) -> list[str]:
    """Map OpenCode tool names to AS tool names.

    Unmapped names pass through unchanged (custom tools).

    Args:
        tools (`list[str]`):
            Tool names in OpenCode format.

    Returns:
        `list[str]`: Tool names in AS format.
    """
    return [BUILTIN_TOOL_MAP.get(t, t) for t in tools]


def parse_opencode_config(
    config: dict[str, Any],
) -> dict[str, Any]:
    """Parse an ``opencode.json`` config fragment into structured data.

    Args:
        config (`dict[str, Any]`):
            The raw config dict (top-level keys: ``agents``,
            ``mcp``, ``skills``, ``permissions``, ``plugins``).

    Returns:
        `dict[str, Any]`: Parsed config with keys:
            - ``agents``: ``dict[str, dict]`` — agent blueprints with
              mapped tool names.
            - ``mcp_servers``: ``dict[str, dict]`` — MCP server configs.
            - ``skills``: ``list[dict]`` — skill directory declarations.
            - ``permissions``: ``dict`` — permission config.
            - ``plugins``: ``list[dict]`` — plugin declarations.
    """
    result: dict[str, Any] = {
        "agents": {},
        "mcp_servers": {},
        "skills": [],
        "permissions": {},
        "plugins": [],
    }

    agents = config.get("agents", {})
    for name, agent_cfg in agents.items():
        parsed = dict(agent_cfg)
        if "tools" in parsed:
            parsed["tools"] = _map_tool_names(parsed["tools"])
        result["agents"][name] = parsed

    mcp = config.get("mcp", {})
    result["mcp_servers"] = dict(mcp)

    skills = config.get("skills", [])
    result["skills"] = list(skills)

    permissions = config.get("permissions", {})
    result["permissions"] = dict(permissions)

    plugins = config.get("plugins", [])
    result["plugins"] = list(plugins)

    return result


class OpenCodeAdapter(ProtocolAdapter):
    """Protocol adapter for the OpenCode SDK.

    Handles conversion between the OpenCode JSON protocol and
    XRuntime's internal models.  Also parses ``opencode.json``
    configuration fragments for declarative agent/skill/MCP/permission
    setup.
    """

    protocol_type = ProtocolType.OPENCODE

    async def parse_request(
        self,
        raw: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> XRuntimeRequest:
        """Parse an OpenCode SDK request.

        The raw body is::

            {"prompt": "...", "agent": "coder",
             "config": {...opencode.json fragment...},
             "session_id": "..."}

        Args:
            raw (`dict`):
                The parsed JSON request body.
            headers (`dict[str, str] | None`):
                Optional HTTP headers.

        Returns:
            `XRuntimeRequest`: The unified request.
        """
        headers = headers or {}
        prompt = raw.get("prompt", "")
        agent_name = raw.get("agent", "")
        session_id = raw.get("session_id")
        inline_config = raw.get("config", {})

        parsed_config = parse_opencode_config(inline_config)

        metadata: dict[str, Any] = {
            "agent_name": agent_name,
            "opencode_config": parsed_config,
        }

        agent_cfg = parsed_config["agents"].get(agent_name, {})
        system_prompt = agent_cfg.get("system_prompt")
        allowed_tools = agent_cfg.get("tools", [])

        perm_cfg = parsed_config.get("permissions", {})
        permission_mode = perm_cfg.get("mode", "default")

        tenant_id = headers.get("x-tenant-id", "default")
        user_id = headers.get("x-user-id", "anonymous")

        return XRuntimeRequest(
            protocol=ProtocolType.OPENCODE,
            prompt=prompt,
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            permission_mode=permission_mode,
            metadata=metadata,
        )

    async def serialize_event_stream(
        self,
        events: AsyncGenerator[dict[str, Any], None],
    ) -> AsyncGenerator[bytes, None]:
        """Serialize an AgentEvent dict stream into OpenCode events.

        Each output chunk is a JSON object followed by ``\\n``.

        Event types produced:
            - ``{"type": "session_start", "session_id": "..."}``
            - ``{"type": "text_delta", "delta": "..."}``
            - ``{"type": "tool_call", "name": "...", "input": {...}}``
            - ``{"type": "session_end", "session_id": "..."}``

        Args:
            events (`AsyncGenerator[dict, None]`):
                Stream of event dicts from the runtime core.

        Yields:
            `bytes`: JSON-encoded event chunks.
        """
        session_id: str | None = None
        tool_call_input: str = ""

        async for event in events:
            event_type = event.get("type", "")

            if event_type == "REPLY_START":
                session_id = event.get("session_id", "")
                yield (
                    json.dumps(
                        {
                            "type": "session_start",
                            "session_id": session_id,
                        }
                    )
                    + "\n"
                ).encode()

            elif event_type == "TEXT_BLOCK_DELTA":
                yield (
                    json.dumps(
                        {
                            "type": "text_delta",
                            "delta": event.get("delta", ""),
                        }
                    )
                    + "\n"
                ).encode()

            elif event_type == "TOOL_CALL_START":
                tool_call_input = ""
                yield (
                    json.dumps(
                        {
                            "type": "tool_call",
                            "name": event.get("tool_call_name", ""),
                            "id": event.get("tool_call_id", ""),
                            "input": {},
                        }
                    )
                    + "\n"
                ).encode()

            elif event_type == "TOOL_CALL_DELTA":
                tool_call_input += event.get("delta", "")

            elif event_type == "TOOL_CALL_END":
                parsed: Any = {}
                if tool_call_input:
                    try:
                        parsed = json.loads(tool_call_input)
                    except json.JSONDecodeError:
                        parsed = {}
                yield (
                    json.dumps(
                        {
                            "type": "tool_call",
                            "id": event.get("tool_call_id", ""),
                            "input": parsed,
                        }
                    )
                    + "\n"
                ).encode()

            elif event_type == "THINKING_BLOCK_DELTA":
                yield (
                    json.dumps(
                        {
                            "type": "thinking_delta",
                            "delta": event.get("delta", ""),
                        }
                    )
                    + "\n"
                ).encode()

            elif event_type == "TOOL_RESULT_START":
                yield (
                    json.dumps(
                        {
                            "type": "tool_result",
                            "id": event.get("tool_call_id", ""),
                            "content": "",
                        }
                    )
                    + "\n"
                ).encode()

            elif event_type == "TOOL_RESULT_TEXT_DELTA":
                yield (
                    json.dumps(
                        {
                            "type": "tool_result_delta",
                            "delta": event.get("delta", ""),
                        }
                    )
                    + "\n"
                ).encode()

            elif event_type == "TOOL_RESULT_END":
                yield (
                    json.dumps(
                        {
                            "type": "tool_result_end",
                            "id": event.get("tool_call_id", ""),
                        }
                    )
                    + "\n"
                ).encode()

            elif event_type == "REPLY_END":
                yield (
                    json.dumps(
                        {
                            "type": "session_end",
                            "session_id": session_id or "",
                        }
                    )
                    + "\n"
                ).encode()
