# -*- coding: utf-8 -*-
"""OpenAI Chat Completions API protocol adapter.

Converts between the official OpenAI Chat Completions wire format
(``POST /v1/chat/completions``) and XRuntime's internal
:class:`XRuntimeRequest` / event stream.

Reference: https://platform.openai.com/docs/api-reference/chat

Inbound (OpenAI → XRuntime):
    - ``messages``: last user message → ``prompt``; full list stored
      in metadata for context reconstruction.
    - First ``role == "system"`` message → ``system_prompt``.
    - ``tools``: passed through unchanged — OpenAI's function-calling
      schema is already the AS internal schema.
    - ``tool_choice``: stored in ``metadata["tool_choice"]``.
    - ``max_tokens`` (default 4096) → ``metadata["max_tokens"]``.
    - ``temperature`` → ``metadata["temperature"]``.
    - ``model`` → ``metadata["model"]``.
    - ``x-session-id`` header: → ``session_id`` (stateful mode).
    - ``x-tenant-id`` header: → ``tenant_id``.
    - ``x-user-id`` header: → ``user_id``.

Outbound (AgentEvent → OpenAI SSE):
    - ``REPLY_START`` → first chunk with ``delta.role = "assistant"``.
    - ``TEXT_BLOCK_DELTA`` → chunk with ``delta.content``.
    - ``TOOL_CALL_START`` → chunk with ``delta.tool_calls`` (start).
    - ``TOOL_CALL_DELTA`` → chunk with ``delta.tool_calls`` (args).
    - ``THINKING_BLOCK_*`` → skipped (OpenAI has no thinking blocks).
    - ``REPLY_END`` → final chunk with ``finish_reason`` +
      ``data: [DONE]`` terminator.

The OpenAI Chat Completions API is stateless by default; XRuntime
makes it stateful via the ``x-session-id`` header. Without it, each
request is a fresh session.
"""
from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from typing import Any

from ._adapter import ProtocolAdapter
from ._request import ProtocolType, XRuntimeRequest


def _extract_text_from_content(
    content: str | list[dict[str, Any]],
) -> str:
    """Extract text from OpenAI message content.

    OpenAI message content is either a plain string or a list of
    content parts (e.g. ``[{"type": "text", "text": "..."}]``).

    Args:
        content (`str | list[dict]`):
            The message content.

    Returns:
        `str`: Concatenated text from all text parts.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "".join(texts)
    return ""


class OpenAIChatAdapter(ProtocolAdapter):
    """Protocol adapter for the OpenAI Chat Completions API.

    Handles conversion between the official
    ``POST /v1/chat/completions`` request/response format and
    XRuntime's internal models.

    The OpenAI tool schema is identical to the AS internal OpenAI
    function-calling schema, so no tool conversion is needed — tools
    pass through unchanged.
    """

    protocol_type = ProtocolType.OPENAI

    async def parse_request(
        self,
        raw: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> XRuntimeRequest:
        """Parse an OpenAI Chat Completions request body.

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

        # Extract system prompt from the first system message
        system_prompt: str | None = None
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = _extract_text_from_content(
                    msg.get("content", ""),
                )
                break

        # Use the last user message as the prompt
        prompt = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                prompt = _extract_text_from_content(
                    msg.get("content", ""),
                )
                break

        # Tools pass through unchanged (OpenAI schema == AS schema)
        tools = raw.get("tools", [])
        if tools is None:
            tools = []

        session_id = headers.get("x-session-id")
        tenant_id = headers.get("x-tenant-id", "default")
        user_id = headers.get("x-user-id", "anonymous")

        metadata: dict[str, Any] = {}
        if tools:
            metadata["tools"] = tools
        metadata["model"] = raw.get("model", "")
        metadata["max_tokens"] = raw.get("max_tokens", 4096)
        metadata["all_messages"] = messages
        if raw.get("tool_choice") is not None:
            metadata["tool_choice"] = raw["tool_choice"]
        if raw.get("temperature") is not None:
            metadata["temperature"] = raw["temperature"]

        return XRuntimeRequest(
            protocol=ProtocolType.OPENAI,
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
        """Serialize an AgentEvent dict stream into OpenAI SSE bytes.

        Produces chunks in the format::

            data: {"id":"...","object":"chat.completion.chunk",...}\\n\\n

        Terminated by::

            data: [DONE]\\n\\n

        The ``finish_reason`` on the final chunk is ``"stop"`` for a
        text-only reply or ``"tool_calls"`` when the reply ended on a
        tool call.

        Args:
            events (`AsyncGenerator[dict, None]`):
                Stream of event dicts from the runtime core.

        Yields:
            `bytes`: SSE-formatted chunks.
        """
        reply_id = "chatcmpl-xruntime"
        created = int(time.time())
        model = ""
        last_block_type: str | None = None
        tool_call_index = -1

        async for event in events:
            event_type = event.get("type", "")

            if event_type == "REPLY_START":
                reply_id = event.get("reply_id", reply_id) or reply_id
                # Emit the first chunk with role=assistant
                chunk = self._build_chunk(
                    reply_id=reply_id,
                    created=created,
                    model=model,
                    delta={"role": "assistant", "content": ""},
                    finish_reason=None,
                )
                yield self._format_sse(chunk)
                last_block_type = "text"

            elif event_type == "TEXT_BLOCK_DELTA":
                delta_text = event.get("delta", "")
                chunk = self._build_chunk(
                    reply_id=reply_id,
                    created=created,
                    model=model,
                    delta={"content": delta_text},
                    finish_reason=None,
                )
                yield self._format_sse(chunk)
                last_block_type = "text"

            elif event_type == "THINKING_BLOCK_DELTA":
                # OpenAI has no thinking blocks — skip silently
                continue

            elif event_type == "TOOL_CALL_START":
                tool_call_index += 1
                tool_call_id = event.get("tool_call_id", "")
                tool_call_name = event.get("tool_call_name", "")
                chunk = self._build_chunk(
                    reply_id=reply_id,
                    created=created,
                    model=model,
                    delta={
                        "tool_calls": [
                            {
                                "index": tool_call_index,
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_call_name,
                                    "arguments": "",
                                },
                            },
                        ],
                    },
                    finish_reason=None,
                )
                yield self._format_sse(chunk)
                last_block_type = "tool_use"

            elif event_type == "TOOL_CALL_DELTA":
                delta_text = event.get("delta", "")
                chunk = self._build_chunk(
                    reply_id=reply_id,
                    created=created,
                    model=model,
                    delta={
                        "tool_calls": [
                            {
                                "index": tool_call_index,
                                "id": tool_call_id,
                                "type": "function",
                                "function": {"arguments": delta_text},
                            },
                        ],
                    },
                    finish_reason=None,
                )
                yield self._format_sse(chunk)

            elif event_type == "REPLY_END":
                finish_reason = (
                    "tool_calls" if last_block_type == "tool_use" else "stop"
                )
                chunk = self._build_chunk(
                    reply_id=reply_id,
                    created=created,
                    model=model,
                    delta={},
                    finish_reason=finish_reason,
                )
                yield self._format_sse(chunk)
                yield b"data: [DONE]\n\n"

            # Other event types (TOOL_RESULT_*, THINKING_BLOCK_START/END,
            # TOOL_CALL_END, EXCEED_MAX_ITERS) are not surfaced in the
            # OpenAI streaming protocol — they're either internal or
            # surface as tool_result messages in a subsequent request.

    @staticmethod
    def _build_chunk(
        *,
        reply_id: str,
        created: int,
        model: str,
        delta: dict[str, Any],
        finish_reason: str | None,
    ) -> dict[str, Any]:
        """Build a single OpenAI chat completion chunk dict.

        Args:
            reply_id (`str`): The completion id.
            created (`int`): Unix timestamp.
            model (`str`): The model name.
            delta (`dict`): The delta content.
            finish_reason (`str | None`): The finish reason or None.

        Returns:
            `dict`: The chunk dict.
        """
        return {
            "id": reply_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                },
            ],
        }

    @staticmethod
    def _format_sse(chunk: dict[str, Any]) -> bytes:
        """Format a chunk dict as an SSE ``data:`` line.

        Args:
            chunk (`dict`): The chunk dict.

        Returns:
            `bytes`: The SSE-formatted chunk.
        """
        return (f"data: {json.dumps(chunk)}\n\n").encode()
