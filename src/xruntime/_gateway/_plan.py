# -*- coding: utf-8 -*-
"""RuntimeExecutionPlan — unified execution plan for all protocols.

Three protocol adapters (Anthropic, Claude Code, OpenCode) produce
an :class:`XRuntimeRequest`, which is then converted to a
:class:`RuntimeExecutionPlan` by :func:`build_plan_from_request`.

The plan carries all execution-relevant fields (model, budget,
workspace, tools, knowledge scope) so downstream code (gateway,
middleware, workspace manager) can make governance decisions
without re-parsing protocol-specific metadata.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ._request import ProtocolType, XRuntimeRequest


class WorkspacePolicy(BaseModel):
    """Workspace backend selection policy.

    Args:
        backend (`str`):
            Workspace backend — ``"local"``, ``"docker"``, ``"e2b"``.
        add_dirs (`list[str]`):
            Additional directories to mount.
    """

    backend: str = "local"
    add_dirs: list[str] = Field(default_factory=list)


class KnowledgeScope(BaseModel):
    """Authorized knowledge-base scope for a request.

    Args:
        kb_ids (`list[str]`):
            Authorized KB ids. Empty means all tenant KBs.
    """

    kb_ids: list[str] = Field(default_factory=list)


class RuntimeExecutionPlan(BaseModel):
    """Unified execution plan produced from any protocol request.

    Args:
        protocol (`ProtocolType`):
            Source protocol.
        tenant_id (`str`):
            Tenant scope (from authenticated principal).
        user_id (`str`):
            User scope.
        session_id (`str | None`):
            Existing session to resume, or None for new.
        agent_name (`str`):
            Target agent blueprint name.
        prompt (`str`):
            User prompt.
        system_prompt (`str | None`):
            System prompt override.
        model_config_name (`str | None`):
            Model to use.
        fallback_model_config_name (`str | None`):
            Fallback model.
        max_turns (`int | None`):
            Max ReAct iterations.
        max_budget_usd (`float | None`):
            Cost budget.
        permission_mode (`str`):
            Permission mode.
        allowed_tools (`list[str]`):
            Tools to allow (intersection of client + tenant).
        disallowed_tools (`list[str]`):
            Tools to deny.
        workspace_policy (`WorkspacePolicy`):
            Workspace backend and mounts.
        knowledge_scope (`KnowledgeScope`):
            Authorized KB ids.
        metadata (`dict[str, Any]`):
            Protocol-specific passthrough metadata.
    """

    protocol: ProtocolType
    tenant_id: str
    user_id: str
    session_id: str | None = None
    agent_name: str = "assistant"
    prompt: str
    system_prompt: str | None = None
    model_config_name: str | None = None
    fallback_model_config_name: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    permission_mode: str = "default"
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)
    workspace_policy: WorkspacePolicy = Field(
        default_factory=WorkspacePolicy,
    )
    knowledge_scope: KnowledgeScope = Field(
        default_factory=KnowledgeScope,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_plan_from_request(
    request: XRuntimeRequest,
    agent_name: str = "assistant",
    tenant_tool_allowlist: set[str] | None = None,
    authorized_kb_ids: list[str] | None = None,
) -> RuntimeExecutionPlan:
    """Build a :class:`RuntimeExecutionPlan` from an
    :class:`XRuntimeRequest`.

    Extracts protocol-specific metadata (Claude Code sandbox/budget/
    model, OpenCode tools/permissions) and maps them to plan fields.
    When ``tenant_tool_allowlist`` is provided, client-supplied
    ``allowed_tools`` are intersected with it so permissions can only
    tighten, never widen.

    Args:
        request (`XRuntimeRequest`):
            The parsed protocol request.
        agent_name (`str`):
            Target agent blueprint name.
        tenant_tool_allowlist (`set[str] | None`):
            Tools the tenant is allowed to use. When provided,
            client ``allowed_tools`` are filtered to this set.
        authorized_kb_ids (`list[str] | None`):
            KB ids the principal is authorized to access.

    Returns:
        `RuntimeExecutionPlan`: The execution plan.
    """
    meta = request.metadata or {}

    # Extract sandbox / workspace backend
    sandbox = meta.get("sandbox", "local")
    add_dirs = meta.get("add_dirs", [])
    workspace_policy = WorkspacePolicy(
        backend=sandbox,
        add_dirs=list(add_dirs) if isinstance(add_dirs, list) else [],
    )

    # Extract budget / model / turns from metadata (Claude Code style)
    max_budget = meta.get("max_budget_usd")
    if max_budget is not None:
        max_budget = float(max_budget)
    model_name = meta.get("model")
    fallback_model = meta.get("fallback_model")
    max_turns = meta.get("max_turns") or request.max_turns

    # Merge allowed/disallowed tools from request and metadata
    raw_allowed = list(request.allowed_tools)
    raw_allowed.extend(meta.get("allowed_tools", []))
    raw_disallowed = list(request.disallowed_tools)
    raw_disallowed.extend(meta.get("disallowed_tools", []))

    # Permissions can only tighten: intersect with tenant allowlist
    if tenant_tool_allowlist is not None:
        allowed = [t for t in raw_allowed if t in tenant_tool_allowlist]
    else:
        allowed = raw_allowed

    # Deduplicate while preserving order
    seen: set[str] = set()
    allowed_tools: list[str] = []
    for t in allowed:
        if t not in seen:
            seen.add(t)
            allowed_tools.append(t)

    seen_d: set[str] = set()
    disallowed_tools: list[str] = []
    for t in raw_disallowed:
        if t not in seen_d:
            seen_d.add(t)
            disallowed_tools.append(t)

    return RuntimeExecutionPlan(
        protocol=request.protocol,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        session_id=request.session_id,
        agent_name=agent_name,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        model_config_name=model_name,
        fallback_model_config_name=fallback_model,
        max_turns=max_turns,
        max_budget_usd=max_budget,
        permission_mode=request.permission_mode,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        workspace_policy=workspace_policy,
        knowledge_scope=KnowledgeScope(
            kb_ids=authorized_kb_ids or [],
        ),
        metadata=dict(meta),
    )
