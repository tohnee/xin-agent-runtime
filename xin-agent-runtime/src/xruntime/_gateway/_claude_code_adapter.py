# -*- coding: utf-8 -*-
"""Claude Code SDK protocol adapter.

Converts between the official Claude Agent SDK wire format and
XRuntime's internal :class:`XRuntimeRequest` / event stream.

Reference: https://docs.claude.com/en/api/agent-sdk/python

Inbound (Claude Code → XRuntime):
    - ``prompt``: → ``prompt``
    - ``options.system_prompt``: → ``system_prompt``
    - ``options.permission_mode``: → ``permission_mode`` (via
      :data:`PERMISSION_MODE_MAP`)
    - ``options.allowed_tools``: → ``allowed_tools``
    - ``options.disallowed_tools``: → ``disallowed_tools``
    - ``options.mcp_servers``: → ``metadata["mcp_servers"]``
    - ``options.agents``: → ``metadata["agents"]`` (subagents)
    - ``options.max_turns``: → ``max_turns``
    - ``options.cwd``: → ``metadata["cwd"]``
    - ``options.resume``: → ``session_id``
    - ``options.can_use_tool``: → ``metadata["can_use_tool"]``
    - ``options.hooks``: → ``metadata["hooks"]``

Outbound (AgentEvent → Claude Code messages):
    - ``REPLY_START`` → ``SystemMessage(subtype="init")``
    - ``TEXT_BLOCK_*`` → ``AssistantMessage`` with ``TextBlock``
    - ``THINKING_BLOCK_*`` → ``AssistantMessage`` with ``ThinkingBlock``
    - ``TOOL_CALL_*`` → ``AssistantMessage`` with ``ToolUseBlock``
    - ``REPLY_END`` → ``ResultMessage(subtype="success")``
    - ``EXCEED_MAX_ITERS`` → ``ResultMessage(subtype="error_max_turns")``
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from ._adapter import ProtocolAdapter
from ._request import ProtocolType, XRuntimeRequest

logger = logging.getLogger("xruntime.gateway.claude_code")

# Max accumulated block content to prevent OOM from malicious upstream.
_MAX_BLOCK_CONTENT_BYTES = 1 * 1024 * 1024  # 1 MB


PERMISSION_MODE_MAP: dict[str, str] = {
    "default": "default",
    "acceptEdits": "accept_edits",
    "bypassPermissions": "bypass",
    "plan": "explore",
    "dontAsk": "dont_ask",
}
"""Maps Claude Code ``permission_mode`` values to AS
:class:`~agentscope.permission.PermissionMode` values."""


def map_claude_options_to_request(
    prompt: str,
    options: dict[str, Any],
) -> XRuntimeRequest:
    """Map a Claude Code ``query(prompt, options)`` call to
    :class:`XRuntimeRequest`.

    Args:
        prompt (`str`):
            The user prompt string.
        options (`dict[str, Any]`):
            The ``ClaudeAgentOptions`` fields as a dict (from
            ``dataclasses.asdict`` or JSON).

    Returns:
        `XRuntimeRequest`: The unified request.
    """
    system_prompt = options.get("system_prompt")
    if isinstance(system_prompt, dict):
        if system_prompt.get("type") == "preset":
            system_prompt = None

    permission_mode = PERMISSION_MODE_MAP.get(
        options.get("permission_mode", "default"),
        "default",
    )

    metadata: dict[str, Any] = {}

    if options.get("mcp_servers"):
        metadata["mcp_servers"] = options["mcp_servers"]
    if options.get("agents"):
        metadata["agents"] = options["agents"]
    if options.get("cwd"):
        metadata["cwd"] = options["cwd"]
    if options.get("can_use_tool"):
        metadata["can_use_tool"] = True
    if options.get("hooks"):
        metadata["hooks"] = True
    if options.get("model"):
        metadata["model"] = options["model"]
    if options.get("fallback_model"):
        metadata["fallback_model"] = options["fallback_model"]
    if options.get("max_budget_usd"):
        metadata["max_budget_usd"] = options["max_budget_usd"]
    if options.get("sandbox"):
        metadata["sandbox"] = options["sandbox"]
    if options.get("plugins"):
        metadata["plugins"] = options["plugins"]
    if options.get("add_dirs"):
        metadata["add_dirs"] = options["add_dirs"]

    session_id = options.get("resume")
    if not session_id and options.get("continue_conversation"):
        session_id = "__continue__"

    return XRuntimeRequest(
        protocol=ProtocolType.CLAUDE_CODE,
        prompt=prompt,
        session_id=session_id,
        system_prompt=system_prompt
        if isinstance(system_prompt, str)
        else None,
        allowed_tools=options.get("allowed_tools") or [],
        disallowed_tools=options.get("disallowed_tools") or [],
        permission_mode=permission_mode,
        max_turns=options.get("max_turns"),
        metadata=metadata,
    )


class ClaudeCodeAdapter(ProtocolAdapter):
    """Protocol adapter for the Claude Code Agent SDK.

    Handles conversion between ``query(prompt, options)`` /
    ``ClaudeSDKClient`` wire format and XRuntime's internal models.
    """

    protocol_type = ProtocolType.CLAUDE_CODE

    async def parse_request(
        self,
        raw: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> XRuntimeRequest:
        """Parse a Claude Code SDK request.

        The raw body is ``{"prompt": "..., "options": {...}}``.

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
        options = raw.get("options", {})

        req = map_claude_options_to_request(prompt, options)

        if headers.get("x-session-id"):
            req.session_id = headers["x-session-id"]
        if headers.get("x-tenant-id"):
            req.tenant_id = headers["x-tenant-id"]
        if headers.get("x-user-id"):
            req.user_id = headers["x-user-id"]

        return req

    async def serialize_event_stream(
        self,
        events: AsyncGenerator[dict[str, Any], None],
    ) -> AsyncGenerator[bytes, None]:
        """Serialize an AgentEvent dict stream into Claude Code messages.

        Each output chunk is a JSON object followed by ``\\n``,
        matching the Claude Code SDK ``Transport.read_messages``
        JSON+newline framing.

        Message types produced:
            - ``{"type": "system", "subtype": "init", ...}``
            - ``{"type": "assistant", "message": {"content": [...]}}``
            - ``{"type": "result", "subtype": "success", ...}``

        Args:
            events (`AsyncGenerator[dict, None]`):
                Stream of event dicts from the runtime core.

        Yields:
            `bytes`: JSON-encoded message chunks.
        """
        session_id: str | None = None
        current_blocks: list[dict[str, Any]] = []
        current_block_id: str | None = None
        current_block_type: str | None = None
        current_block_content: str = ""
        has_exceeded = False

        async for event in events:
            event_type = event.get("type", "")

            if event_type == "REPLY_START":
                session_id = event.get("session_id", "")
                yield (
                    json.dumps(
                        {
                            "type": "system",
                            "subtype": "init",
                            "session_id": session_id,
                            "data": {"session_id": session_id},
                        },
                    )
                    + "\n"
                ).encode()

            elif event_type == "TEXT_BLOCK_START":
                current_block_id = event.get("block_id", "")
                current_block_type = "text"
                current_block_content = ""

            elif event_type == "THINKING_BLOCK_START":
                current_block_id = event.get("block_id", "")
                current_block_type = "thinking"
                current_block_content = ""

            elif event_type == "TOOL_CALL_START":
                current_block_id = event.get("tool_call_id", "")
                current_block_type = "tool_use"
                current_block_content = ""
                tool_name = event.get("tool_call_name", "")
                current_blocks.append(
                    {
                        "type": "tool_use",
                        "id": current_block_id,
                        "name": tool_name,
                        "input": {},
                    },
                )

            elif event_type in (
                "TEXT_BLOCK_DELTA",
                "THINKING_BLOCK_DELTA",
            ):
                delta = event.get("delta", "")
                if (
                    len(current_block_content) + len(delta)
                    > _MAX_BLOCK_CONTENT_BYTES
                ):
                    logger.warning(
                        "block content exceeds %d bytes, " "truncating delta",
                        _MAX_BLOCK_CONTENT_BYTES,
                    )
                    remaining = _MAX_BLOCK_CONTENT_BYTES - len(
                        current_block_content
                    )
                    if remaining > 0:
                        current_block_content += delta[:remaining]
                else:
                    current_block_content += delta

            elif event_type == "TOOL_CALL_DELTA":
                delta = event.get("delta", "")
                if (
                    len(current_block_content) + len(delta)
                    > _MAX_BLOCK_CONTENT_BYTES
                ):
                    logger.warning(
                        "tool call content exceeds %d bytes, "
                        "truncating delta",
                        _MAX_BLOCK_CONTENT_BYTES,
                    )
                    remaining = _MAX_BLOCK_CONTENT_BYTES - len(
                        current_block_content
                    )
                    if remaining > 0:
                        current_block_content += delta[:remaining]
                else:
                    current_block_content += delta

            elif event_type == "TEXT_BLOCK_END":
                block = {"type": "text", "text": current_block_content}
                if current_block_type == "text":
                    current_blocks.append(block)
                yield (
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": [block],
                            },
                        },
                    )
                    + "\n"
                ).encode()
                current_block_id = None
                current_block_type = None

            elif event_type == "THINKING_BLOCK_END":
                block = {
                    "type": "thinking",
                    "thinking": current_block_content,
                }
                if current_block_type == "thinking":
                    current_blocks.append(block)
                yield (
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": [block],
                            },
                        },
                    )
                    + "\n"
                ).encode()
                current_block_id = None
                current_block_type = None

            elif event_type == "TOOL_CALL_END":
                parsed_input: dict[str, Any] = {}
                if current_block_content:
                    try:
                        parsed_input = json.loads(current_block_content)
                    except json.JSONDecodeError:
                        parsed_input = {}
                tool_block: dict[str, Any] | None = None
                for b in current_blocks:
                    if (
                        b.get("type") == "tool_use"
                        and b.get("id") == current_block_id
                    ):
                        b["input"] = parsed_input
                        tool_block = b
                        break
                if tool_block is None:
                    tool_block = {
                        "type": "tool_use",
                        "id": current_block_id or "",
                        "name": "",
                        "input": parsed_input,
                    }
                yield (
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": [tool_block],
                            },
                        },
                    )
                    + "\n"
                ).encode()
                current_block_id = None
                current_block_type = None

            elif event_type == "EXCEED_MAX_ITERS":
                has_exceeded = True

            elif event_type == "REPLY_END":
                current_blocks = []
                subtype = "error_max_turns" if has_exceeded else "success"
                yield (
                    json.dumps(
                        {
                            "type": "result",
                            "subtype": subtype,
                            "result": "",
                            "session_id": session_id or "",
                            "total_cost_usd": 0.0,
                            "duration_ms": 0,
                            "num_turns": 0,
                            "is_error": has_exceeded,
                        },
                    )
                    + "\n"
                ).encode()
