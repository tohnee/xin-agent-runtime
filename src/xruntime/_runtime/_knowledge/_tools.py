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


class SearchKnowledgeTool(ToolBase):
    """Tool that lets the agent search the knowledge base.

    Args:
        registry (`KnowledgeRegistry`):
            The knowledge registry to search.
        top_k (`int`):
            Default number of results.
        tenant_id (`str`):
            Default tenant scope.
    """

    def __init__(
        self,
        registry: KnowledgeRegistry,
        top_k: int = 5,
        tenant_id: str = "default",
    ) -> None:
        """Initialize the tool."""
        self.registry = registry
        self._top_k = top_k
        self._tenant_id = tenant_id

    @property
    def tool_call_name(self) -> str:
        """Return the tool name."""
        return "search_knowledge"

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for knowledge search (read-only).

        Searching the knowledge base is read-only, so this returns
        ``PASSTHROUGH`` to let the permission engine continue with
        rule matching.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data.
            context (`PermissionContext`):
                The active permission context.

        Returns:
            `PermissionDecision`: A passthrough decision.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Knowledge search is read-only.",
        )

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
    """

    def __init__(
        self,
        registry: KnowledgeRegistry,
        tenant_id: str = "default",
    ) -> None:
        """Initialize the tool."""
        self.registry = registry
        self._tenant_id = tenant_id

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

        Ingestion mutates the knowledge base, so this returns
        ``PASSTHROUGH`` to let the permission engine apply its rules
        (e.g. deny ``ingest_knowledge`` for read-only roles).

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data.
            context (`PermissionContext`):
                The active permission context.

        Returns:
            `PermissionDecision`: A passthrough decision.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Knowledge ingestion writes to the knowledge base.",
        )

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
