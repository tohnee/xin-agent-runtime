# -*- coding: utf-8 -*-
"""Unified internal request model for XRuntime.

Every protocol adapter converts its inbound request into an
:class:`XRuntimeRequest`, which the runtime core consumes uniformly.
This keeps the kernel protocol-neutral.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProtocolType(str, Enum):
    """The three supported ingress protocols.

    Values:
        ANTHROPIC: Anthropic Messages API (``POST /v1/messages``).
        CLAUDE_CODE: Claude Code Agent SDK (``Transport`` or HTTP).
        OPENCODE: OpenCode SDK.
    """

    ANTHROPIC = "anthropic"
    CLAUDE_CODE = "claude_code"
    OPENCODE = "opencode"


class ToolExecutionMode(str, Enum):
    """Where tools are executed.

    Values:
        SERVER: Tools execute server-side via the AgentScope Toolkit.
        EXTERNAL: Tool results are delivered by the client via HITL
            external execution events.
    """

    SERVER = "server"
    EXTERNAL = "external"


class XRuntimeRequest(BaseModel):
    """The unified request model for all three protocols.

    Each adapter's ``parse_request`` produces an instance of this
    class.  The runtime core reads from it without knowing which
    protocol originated the call.

    Args:
        protocol (`ProtocolType`):
            Which protocol produced this request.
        prompt (`str`):
            The user prompt or input text.
        session_id (`str | None`):
            Existing session to resume.  ``None`` creates a new
            session.
        user_id (`str`):
            User identifier.  Defaults to ``"anonymous"``.
        tenant_id (`str`):
            Tenant identifier for multi-tenant isolation.  Defaults
            to ``"default"``.
        system_prompt (`str | None`):
            System prompt override.  ``None`` uses the agent's
            default.
        allowed_tools (`list[str]`):
            Tools to auto-approve (mapped to permission allow rules).
        disallowed_tools (`list[str]`):
            Tools to deny (mapped to permission deny rules).
        permission_mode (`str`):
            Permission mode — ``"default"``, ``"accept_edits"``,
            ``"explore"``, ``"bypass"``, ``"dont_ask"``.
        tool_mode (`ToolExecutionMode`):
            Where tools execute — server-side or external.
        max_turns (`int | None`):
            Maximum ReAct iterations.  ``None`` uses agent default.
        metadata (`dict[str, Any]`):
            Protocol-specific metadata carried through.
    """

    protocol: ProtocolType
    prompt: str
    session_id: str | None = None
    user_id: str = "anonymous"
    tenant_id: str = "default"
    system_prompt: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)
    permission_mode: str = "default"
    tool_mode: ToolExecutionMode = ToolExecutionMode.SERVER
    max_turns: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
