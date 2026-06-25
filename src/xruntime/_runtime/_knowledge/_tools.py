# -*- coding: utf-8 -*-
"""Knowledge tools — agent-callable tools for KB interaction.

When the :class:`KnowledgeMiddleware` is in ``"agent_control"`` or
``"both"`` mode, these tools are exposed to the agent so it can
decide when to search or ingest knowledge.
"""
from __future__ import annotations

from typing import Any

from agentscope.tool import ToolBase, ToolResponse
from agentscope.message import TextBlock
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)

from ._base import KnowledgeQuery
from ._registry import KnowledgeRegistry


def _check_tenant_action(
    role: str,
    action: str,
) -> PermissionDecision:
    """Check a tenant-level RBAC action for a knowledge tool.

    Args:
        role (`str`): The principal's role (owner/admin/contributor/viewer).
        action (`str`): The action to check (kb:query, doc:ingest).

    Returns:
        `PermissionDecision`: ALLOW if the role has the action, DENY otherwise.
    """
    from .._tenant import Action, TenantPolicy, TenantRole

    try:
        normalized_role = TenantRole(role)
    except ValueError:
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message=f"Unknown role: {role}",
        )
    policy = TenantPolicy.default()
    principal = type(
        "P",
        (),
        {"role": normalized_role},
    )()
    try:
        normalized_action = Action(action)
    except ValueError:
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message=f"Unknown action: {action}",
        )
    decision = policy.check(principal, normalized_action)
    if decision.allowed:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=decision.reason,
        )
    return PermissionDecision(
        behavior=PermissionBehavior.DENY,
        message=decision.reason,
    )


class SearchKnowledgeTool(ToolBase):
    """Tool that lets the agent search the knowledge base.

    Args:
        registry (`KnowledgeRegistry`):
            The knowledge registry to search.
        top_k (`int`):
            Default number of results.
        tenant_id (`str`):
            Default tenant scope.
        user_id (`str`):
            User scope for RBAC-aware ingestion.
        kb_ids (`list[str]`):
            Target knowledge-base ids. The first id is written as
            ``kb_id`` metadata when present.
        user_id (`str`):
            User scope for RBAC-aware retrieval.
        kb_ids (`list[str]`):
            Authorized knowledge-base ids.
    """

    def __init__(
        self,
        registry: KnowledgeRegistry,
        top_k: int = 5,
        tenant_id: str = "default",
        user_id: str = "",
        kb_ids: list[str] | None = None,
        role: str = "viewer",
    ) -> None:
        """Initialize the tool."""
        self.registry = registry
        self._top_k = top_k
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._kb_ids = kb_ids or []
        self._role = role

    @property
    def tool_call_name(self) -> str:
        """Return the tool name."""
        return "search_knowledge"

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for knowledge search.

        Enforces tenant RBAC: the principal's role must have
        ``kb:query`` action. Viewer and above can search.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data.
            context (`PermissionContext`):
                The active permission context.

        Returns:
            `PermissionDecision`: ALLOW if role has kb:query, else DENY.
        """
        return _check_tenant_action(self._role, "kb:query")

    async def __call__(
        self,
        query: str,
        top_k: int | None = None,
    ) -> ToolResponse:
        """Search the knowledge base.

        Args:
            query (`str`):
                The search query.
            top_k (`int | None`):
                Number of results (defaults to the tool's default).

        Returns:
            `ToolResponse`: Search results as formatted text.
        """
        kb_query = KnowledgeQuery(
            query=query,
            top_k=top_k or self._top_k,
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            kb_ids=list(self._kb_ids),
        )
        result = await self.registry.retrieve(kb_query)
        text = result.to_context_text()
        if not text:
            text = "No relevant knowledge found."
        return ToolResponse(
            content=[TextBlock(type="text", text=text)],
            metadata={
                "total_found": result.total_found,
                "latency_ms": result.latency_ms,
            },
        )


class IngestKnowledgeTool(ToolBase):
    """Tool that lets the agent ingest new knowledge.

    Args:
        registry (`KnowledgeRegistry`):
            The knowledge registry to ingest into.
        tenant_id (`str`):
            Default tenant scope.
        user_id (`str`):
            User scope for RBAC-aware ingestion.
        kb_ids (`list[str]`):
            Target knowledge-base ids. The first id is written as
            ``kb_id`` metadata when present.
    """

    def __init__(
        self,
        registry: KnowledgeRegistry,
        tenant_id: str = "default",
        user_id: str = "",
        kb_ids: list[str] | None = None,
        role: str = "viewer",
    ) -> None:
        """Initialize the tool."""
        self.registry = registry
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._kb_ids = kb_ids or []
        self._role = role

    @property
    def tool_call_name(self) -> str:
        """Return the tool name."""
        return "ingest_knowledge"

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for knowledge ingestion (write).

        Enforces tenant RBAC: the principal's role must have
        ``doc:ingest`` action. Contributor and above can ingest.
        Viewer is denied.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data.
            context (`PermissionContext`):
                The active permission context.

        Returns:
            `PermissionDecision`: ALLOW if role has doc:ingest, else DENY.
        """
        return _check_tenant_action(self._role, "doc:ingest")

    async def __call__(
        self,
        content: str,
        title: str = "",
        source_type: str = "text",
        source_id: str = "",
    ) -> ToolResponse:
        """Ingest a document into the knowledge base.

        Args:
            content (`str`):
                The content to ingest.
            title (`str`):
                Title for the source.
            source_type (`str`):
                Type of source (text, markdown, code).
            source_id (`str`):
                Optional source ID (auto-generated if empty).

        Returns:
            `ToolResponse`: Ingestion confirmation.
        """
        import uuid

        if not source_id:
            source_id = str(uuid.uuid4())

        await self.registry.ingest(
            source_id=source_id,
            content=content,
            title=title,
            source_type=source_type,
            metadata={
                "tenant_id": self._tenant_id,
                "user_id": self._user_id,
                "kb_id": self._kb_ids[0] if self._kb_ids else "default",
            },
        )

        if self.registry.backends:
            for backend in self.registry.backends:
                if backend.config.auto_compile:
                    await self.registry.compile()
                    break

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Knowledge ingested successfully "
                    f"(source_id: {source_id}).",
                ),
            ],
            metadata={"source_id": source_id},
        )
