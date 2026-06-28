# -*- coding: utf-8 -*-
"""Knowledge middleware — auto-injects retrieved knowledge.

This :class:`agentscope.middleware.MiddlewareBase` subclass
intercepts the agent's reply cycle and injects relevant knowledge
context before the LLM generates a response.

Two modes:

- ``"static_control"`` (default) — automatically retrieves knowledge
  matching the user's input and injects it as a system hint before
  each reply. The agent has no direct control over retrieval.
- ``"agent_control"`` — exposes ``search_knowledge`` and
  ``ingest_knowledge`` tools so the agent can decide when to
  retrieve. No auto-injection.

The middleware delegates to :class:`KnowledgeRegistry` for the
actual retrieval/ingestion operations.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase
from agentscope.message import Msg, HintBlock, AssistantMsg
from agentscope.event import (
    ReplyStartEvent,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)

from ._base import KnowledgeQuery
from ._registry import KnowledgeRegistry

logger = logging.getLogger(__name__)


def _extract_query_text(inputs: Any) -> str:
    """Pull the user's query text out of the agent reply inputs.

    Mirrors the AS ``on_reply`` contract: ``input_kwargs["inputs"]``
    is a ``Msg | list[Msg]`` (a new user turn) or a resumption event
    (``UserConfirmResultEvent`` / ``ExternalExecutionResultEvent``).
    Resumption events and non-user messages yield no query text.

    Args:
        inputs (`Any`):
            The unified reply inputs from ``input_kwargs["inputs"]``.

    Returns:
        `str`: The concatenated user text, or ``""`` if none.
    """
    if inputs is None:
        return ""
    if isinstance(
        inputs,
        (ExternalExecutionResultEvent, UserConfirmResultEvent),
    ):
        return ""

    msgs = inputs if isinstance(inputs, list) else [inputs]
    texts: list[str] = []
    for msg in msgs:
        if not isinstance(msg, Msg) or msg.role != "user":
            continue
        text = msg.get_text_content()
        if text:
            texts.append(text)
    return "\n".join(texts)


class KnowledgeMiddleware(MiddlewareBase):
    """Middleware that injects knowledge into agent context.

    Args:
        registry (`KnowledgeRegistry`):
            The knowledge registry to retrieve from.
        mode (`str`):
            Retrieval mode — ``"static_control"`` (auto-inject),
            ``"agent_control"`` (tools only), or ``"both"``.
        top_k (`int`):
            Default number of chunks to retrieve.
        tenant_id (`str`):
            Default tenant scope for retrieval.
        user_id (`str`):
            User scope for RBAC-aware knowledge retrieval.
        kb_ids (`list[str]`):
            Authorized knowledge-base ids for retrieval. Empty means
            all KBs in the tenant are eligible.
        hint_template (`str`):
            Jinja2 template for the hint message. Receives
            ``{{ context }}`` (the formatted knowledge text).
    """

    DEFAULT_HINT_TEMPLATE = (
        "The following knowledge may be relevant to the user's "
        "request. Use it to inform your response:\n\n"
        "{{ context }}"
    )

    def __init__(
        self,
        registry: KnowledgeRegistry,
        mode: str = "static_control",
        top_k: int = 5,
        tenant_id: str = "default",
        user_id: str = "",
        kb_ids: list[str] | None = None,
        hint_template: str | None = None,
        role: str = "viewer",
    ) -> None:
        """Initialize the middleware."""
        self.registry = registry
        self.mode = mode
        self.top_k = top_k
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.kb_ids = kb_ids or []
        self.hint_template = hint_template or self.DEFAULT_HINT_TEMPLATE
        self.role = role

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Inject knowledge before the LLM generates a reply.

        In ``static_control`` / ``both`` mode, retrieves knowledge
        matching the user's input and appends a synthetic hint message
        into ``agent.state.context`` immediately after the user
        message has been ingested (signalled by ``ReplyStartEvent``),
        mirroring AS's own mem0 middleware. In ``agent_control`` mode
        this hook is a pass-through (the agent uses the search/ingest
        tools instead).

        Args:
            agent (`Agent`):
                The agent instance.
            input_kwargs (`dict`):
                The AS reply input kwargs, containing the ``inputs``
                key (``Msg | list[Msg]`` or a resumption event).
            next_handler (`Callable`):
                The next middleware or ``_reply_impl``.

        Yields:
            Reply chunks / events from the agent.
        """
        if self.mode == "agent_control":
            async for chunk in next_handler(**input_kwargs):
                yield chunk
            return

        query_text = _extract_query_text(input_kwargs.get("inputs"))
        hint_msg = await self._build_hint(query_text)

        injected = False
        async for chunk in next_handler(**input_kwargs):
            if (
                not injected
                and hint_msg is not None
                and isinstance(chunk, ReplyStartEvent)
            ):
                agent.state.context.append(hint_msg)
                injected = True
            yield chunk

    async def _build_hint(self, query_text: str) -> AssistantMsg | None:
        """Retrieve knowledge and build a hint message, or None.

        Args:
            query_text (`str`):
                The user's query text. Empty input short-circuits to
                ``None`` (no retrieval).

        Returns:
            `AssistantMsg | None`: An assistant-role message carrying a
            single :class:`HintBlock`, or ``None`` when there is
            nothing to inject.
        """
        if not query_text.strip():
            logger.debug(
                f"[KNOWLEDGE-RETRIEVE] Empty query text, skipping",
            )
            return None

        query = KnowledgeQuery(
            query=query_text,
            top_k=self.top_k,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            kb_ids=list(self.kb_ids),
        )

        logger.info(
            f"[KNOWLEDGE-RETRIEVE] tenant={self.tenant_id}, "
            f"user={self.user_id}, kb_ids={self.kb_ids}, "
            f"top_k={self.top_k}, query='{query_text[:100]}...'",
        )

        try:
            result = await self.registry.retrieve(query)
            logger.info(
                f"[KNOWLEDGE-RESULT] tenant={self.tenant_id}, "
                f"total_found={result.total_found}, "
                f"chunks_returned={len(result.chunks)}, "
                f"latency_ms={result.latency_ms}",
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"[KNOWLEDGE-ERROR] tenant={self.tenant_id}, "
                f"retrieval failed: {e}",
                exc_info=True,
            )
            return None

        context_text = result.to_context_text()
        if not context_text:
            logger.info(
                f"[KNOWLEDGE-EMPTY] tenant={self.tenant_id}, "
                f"no relevant knowledge found for query",
            )
            return None

        logger.info(
            f"[KNOWLEDGE-INJECT] tenant={self.tenant_id}, "
            f"context_length={len(context_text)} chars",
        )

        hint_text = self.hint_template.replace(
            "{{ context }}",
            context_text,
        )

        return AssistantMsg(
            name="knowledge",
            content=[HintBlock(hint=hint_text)],
        )

    async def list_tools(self) -> list[Any]:
        """Expose knowledge tools in ``agent_control`` / ``both`` mode.

        In ``static_control`` mode the agent has no direct control over
        retrieval, so no tools are exposed. In ``agent_control`` or
        ``both`` mode the ``search_knowledge`` and ``ingest_knowledge``
        tools are returned so the agent can decide when to retrieve or
        ingest.

        Returns:
            `list[ToolBase]`: The knowledge tools, or an empty list.
        """
        if self.mode not in ("agent_control", "both"):
            return []
        from ._tools import SearchKnowledgeTool, IngestKnowledgeTool

        return [
            SearchKnowledgeTool(
                registry=self.registry,
                top_k=self.top_k,
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                kb_ids=list(self.kb_ids),
                role=self.role,
            ),
            IngestKnowledgeTool(
                registry=self.registry,
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                kb_ids=list(self.kb_ids),
                role=self.role,
            ),
        ]
