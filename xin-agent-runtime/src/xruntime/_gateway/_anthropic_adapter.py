# -*- coding: utf-8 -*-
"""Anthropic Messages API protocol adapter.

Converts between the official Anthropic Messages API wire format
(``POST /v1/messages``) and XRuntime's internal
:class:`XRuntimeRequest` / event stream.

Reference: https://docs.anthropic.com/en/api/messages

Inbound (Anthropic → XRuntime):
    - ``messages``: last user message → ``prompt``; full list stored
      in metadata for context reconstruction.
    - ``system``: → ``system_prompt``.
    - ``tools``: converted from Anthropic schema to AS OpenAI
      function-calling schema, stored in ``metadata["tools"]``.
    - ``x-session-id`` header: → ``session_id`` (stateful mode).
    - ``x-tenant-id`` header: → ``tenant_id``.

Outbound (AgentEvent → Anthropic SSE):
    - ``REPLY_START`` → ``message_start``
    - ``TEXT_BLOCK_*`` → ``content_block_start/delta/stop`` (text)
    - ``THINKING_BLOCK_*`` → ``content_block_start/delta/stop``
      (thinking)
    - ``TOOL_CALL_*`` → ``content_block_start/delta/stop`` (tool_use)
    - ``REPLY_END`` → ``message_delta`` + ``message_stop``

The Anthropic API is stateless by default; XRuntime makes it stateful
via the ``x-session-id`` header.  Without it, each request is a
fresh session.
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from ._adapter import ProtocolAdapter
from ._request import ProtocolType, XRuntimeRequest


def convert_anthropic_tools_to_as(
    anthropic_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Anthropic tool definitions to AS OpenAI function schema.

    Anthropic format::

        {"name": "...", "description": "...", "input_schema": {...}}

    AS format::

        {"type": "function", "function": {
            "name": "...", "description": "...", "parameters": {...}
        }}

    Args:
        anthropic_tools (`list[dict]`):
            Tool definitions in Anthropic format.

    Returns:
        `list[dict]`: Tool definitions in AS OpenAI function format.
    """
    result: list[dict[str, Any]] = []
    for tool in anthropic_tools:
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get(
                        "input_schema",
                        {"type": "object", "properties": {}},
                    ),
                },
            },
        )
    return result


def convert_as_tools_to_anthropic(
    as_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert AS OpenAI function schema to Anthropic tool definitions.

    Args:
        as_tools (`list[dict]`):
            Tool definitions in AS OpenAI function format.

    Returns:
        `list[dict]`: Tool definitions in Anthropic format.
    """
    result: list[dict[str, Any]] = []
    for tool in as_tools:
        func = tool.get("function", tool)
        result.append(
            {
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get(
                    "parameters",
                    {"type": "object", "properties": {}},
                ),
            },
        )
    return result


def _extract_text_from_content(
    content: str | list[dict[str, Any]],
) -> str:
    """Extract text from Anthropic content (string or block list).

    Args:
        content (`str | list[dict]`):
            Anthropic message content — either a plain string or a
            list of content blocks.

    Returns:
        `str`: Concatenated text from all text blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return " ".join(texts)
    return ""


class AnthropicMessagesAdapter(ProtocolAdapter):
    """Protocol adapter for the Anthropic Messages API.

    Handles conversion between the official ``POST /v1/messages``
    request/response format and XRuntime's internal models.
    """

    protocol_type = ProtocolType.ANTHROPIC

    async def parse_request(
        self,
        raw: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> XRuntimeRequest:
        """Parse an Anthropic Messages API request body.

        Args:
            raw (`dict`):
                The parsed JSON request body.
            headers (`dict[str, str] | None`):
                Optional HTTP headers for session/tenant routing.

        Returns:
            `XRuntimeRequest`: The unified request.
        """
        headers = headers or {}
        messages = raw.get("messages", [])

        prompt = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                prompt = _extract_text_from_content(
                    msg.get("content", ""),
                )
                break

        system_prompt = raw.get("system")
        if isinstance(system_prompt, list):
            system_prompt = " ".join(
                b.get("text", "")
                for b in system_prompt
                if isinstance(b, dict) and b.get("type") == "text"
            )

        tools = convert_anthropic_tools_to_as(
            raw.get("tools", []),
        )

        session_id = headers.get("x-session-id")
        tenant_id = headers.get("x-tenant-id", "default")
        user_id = headers.get("x-user-id", "anonymous")

        metadata: dict[str, Any] = {}
        if tools:
            metadata["tools"] = tools
        metadata["model"] = raw.get("model", "")
        metadata["max_tokens"] = raw.get("max_tokens", 4096)
        metadata["all_messages"] = messages
        if raw.get("tool_choice"):
            metadata["tool_choice"] = raw["tool_choice"]

        return XRuntimeRequest(
            protocol=ProtocolType.ANTHROPIC,
            prompt=prompt,
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            system_prompt=system_prompt if system_prompt else None,
            metadata=metadata,
        )

    async def serialize_event_stream(
        self,
        events: AsyncGenerator[dict[str, Any], None],
    ) -> AsyncGenerator[bytes, None]:
        """Serialize an AgentEvent dict stream into Anthropic SSE bytes.

        Each output chunk is a JSON object followed by ``\\n``,
        matching the Anthropic streaming SSE event format (without
        the ``event:`` / ``data:`` SSE framing — the gateway can
        add that layer if needed).

        Args:
            events (`AsyncGenerator[dict, None]`):
                Stream of event dicts from the runtime core.

        Yields:
            `bytes`: JSON-encoded SSE event chunks.
        """
        block_index = 0
        block_type_map: dict[str, str] = {}
        last_block_type: str | None = None

        async for event in events:
            event_type = event.get("type", "")
            if event_type == "REPLY_START":
                # Reset per-reply block state so a second reply flowing
                # through the same stream / reused adapter instance does
                # not start from the previous reply's residual index.
                block_index = 0
                block_type_map = {}
                last_block_type = None
            elif event_type == "TEXT_BLOCK_START":
                last_block_type = "text"
            elif event_type == "THINKING_BLOCK_START":
                last_block_type = "thinking"
            elif event_type == "TOOL_CALL_START":
                last_block_type = "tool_use"
            chunk = self._convert_event(
                event,
                event_type,
                block_index,
                block_type_map,
                last_block_type,
            )
            if chunk is not None:
                followed_by_stop = chunk.pop("_followed_by_stop", False)
                increment = chunk.pop("_increment_index", False)
                if increment:
                    block_index += 1
                yield (json.dumps(chunk) + "\n").encode()
                if followed_by_stop:
                    yield (
                        json.dumps({"type": "message_stop"}) + "\n"
                    ).encode()

    def _convert_event(
        self,
        event: dict[str, Any],
        event_type: str,
        block_index: int,
        block_type_map: dict[str, str],
        last_block_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Convert a single AgentEvent dict to an Anthropic SSE event.

        Args:
            event (`dict`):
                The event dict.
            event_type (`str`):
                The event type string.
            block_index (`int`):
                Current content block index.
            block_type_map (`dict[str, str]`):
                Maps block_id → content type for delta/stop lookup.

        Returns:
            `dict | None`: The Anthropic SSE event dict, or ``None``
            if the event should be skipped.
        """
        if event_type == "REPLY_START":
            return {
                "type": "message_start",
                "message": {
                    "id": event.get("reply_id", ""),
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                },
                "_increment_index": False,
            }

        if event_type == "TEXT_BLOCK_START":
            block_id = event.get("block_id", "")
            block_type_map[block_id] = "text"
            return {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {
                    "type": "text",
                    "text": "",
                },
                "_increment_index": False,
            }

        if event_type == "THINKING_BLOCK_START":
            block_id = event.get("block_id", "")
            block_type_map[block_id] = "thinking"
            return {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {
                    "type": "thinking",
                    "thinking": "",
                },
                "_increment_index": False,
            }

        if event_type == "TOOL_CALL_START":
            tool_call_id = event.get("tool_call_id", "")
            block_type_map[tool_call_id] = "tool_use"
            return {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_call_id,
                    "name": event.get("tool_call_name", ""),
                    "input": {},
                },
                "_increment_index": False,
            }

        if event_type in (
            "TEXT_BLOCK_DELTA",
            "THINKING_BLOCK_DELTA",
        ):
            block_id = event.get("block_id", "")
            content_type = block_type_map.get(block_id, "text")
            delta_key = "text" if content_type == "text" else "thinking"
            return {
                "type": "content_block_delta",
                "index": block_index,
                "delta": {
                    "type": delta_key + "_delta",
                    delta_key: event.get("delta", ""),
                },
                "_increment_index": False,
            }

        if event_type == "TOOL_CALL_DELTA":
            return {
                "type": "content_block_delta",
                "index": block_index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": event.get("delta", ""),
                },
                "_increment_index": False,
            }

        if event_type in (
            "TEXT_BLOCK_END",
            "THINKING_BLOCK_END",
            "TOOL_CALL_END",
        ):
            return {
                "type": "content_block_stop",
                "index": block_index,
                "_increment_index": True,
            }

        # Tool result events — Anthropic returns these as tool_result
        # content blocks in a user message.  In XRuntime's NDJSON
        # format, we emit them as content blocks with type=tool_result
        # so clients can reconstruct the full conversation.
        if event_type == "TOOL_RESULT_START":
            return {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {
                    "type": "tool_result",
                    "tool_use_id": event.get("tool_call_id", ""),
                    "content": "",
                },
                "_increment_index": False,
            }

        if event_type == "TOOL_RESULT_TEXT_DELTA":
            return {
                "type": "content_block_delta",
                "index": block_index,
                "delta": {
                    "type": "text_delta",
                    "text": event.get("delta", ""),
                },
                "_increment_index": False,
            }

        if event_type == "TOOL_RESULT_END":
            return {
                "type": "content_block_stop",
                "index": block_index,
                "_increment_index": True,
            }

        if event_type == "REPLY_END":
            if last_block_type == "tool_use":
                stop_reason = "tool_use"
            elif last_block_type == "thinking":
                stop_reason = "end_turn"
            else:
                stop_reason = "end_turn"
            return {
                "type": "message_delta",
                "delta": {
                    "stop_reason": stop_reason,
                    "stop_sequence": None,
                },
                "usage": {
                    "output_tokens": 0,
                },
                "_increment_index": False,
                "_followed_by_stop": True,
            }

        return None
