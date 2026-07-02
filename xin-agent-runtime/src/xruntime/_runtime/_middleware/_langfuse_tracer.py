# -*- coding: utf-8 -*-
"""Langfuse tracer middleware — connects LangfuseExporter to the
middleware chain so model calls and tool calls are traced.

When Langfuse is disabled or not installed, the exporter is a
no-op, so this middleware has zero overhead.
"""
from __future__ import annotations

import time
from typing import Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase

from .._langfuse import LangfuseExporter


class LangfuseTracerMiddleware(MiddlewareBase):
    """Trace model calls and tool calls to Langfuse.

    Args:
        exporter (`LangfuseExporter`):
            The Langfuse exporter. When ``is_noop`` is True,
            all trace calls are skipped.
        tenant_id (`str`):
            Tenant scope for traces.
        user_id (`str`):
            User scope for traces.
        session_id (`str`):
            Session scope for traces.
    """

    def __init__(
        self,
        exporter: LangfuseExporter,
        tenant_id: str = "",
        user_id: str = "",
        session_id: str = "",
    ) -> None:
        """Initialize the tracer middleware."""
        self._exporter = exporter
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._session_id = session_id
        self._turn: int = 0
        self._tool_start: float = 0.0
        self._model_start: float = 0.0

    async def on_reasoning(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Trace the reasoning/model-call phase.

        Records a model trace with ``success=False`` and re-raises
        when ``next_handler`` raises.
        """
        self._turn += 1
        self._model_start = time.time()
        success = True
        try:
            async for event in next_handler():
                yield event
        except BaseException:
            success = False
            raise
        finally:
            duration_ms = (time.time() - self._model_start) * 1000
            self._trace_model(agent, duration_ms, success)

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Trace tool call execution.

        Records a tool trace with ``success=False`` and re-raises
        when ``next_handler`` raises.
        """
        tool_call = input_kwargs.get("tool_call")
        tool_name = getattr(tool_call, "name", "unknown")
        self._tool_start = time.time()

        success = True
        try:
            async for event in next_handler():
                yield event
        except BaseException:
            success = False
            raise
        finally:
            duration_ms = (time.time() - self._tool_start) * 1000
            self._trace_tool(tool_name, duration_ms, success)

    def _trace_model(
        self,
        agent: Any,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        """Trace a model generation.

        Args:
            agent (`Any`): The agent whose model was called.
            duration_ms (`float`): Wall-clock duration in milliseconds.
            success (`bool`): Whether the model call completed
                without raising. Defaults to ``True``.
        """
        if self._exporter.is_noop:
            return
        model_name = ""
        if hasattr(agent, "model"):
            model_obj = agent.model
            if hasattr(model_obj, "model_name"):
                model_name = model_obj.model_name
            elif hasattr(model_obj, "name"):
                model_name = model_obj.name
        self._exporter.trace_generation(
            model=model_name or "unknown",
            input_tokens=0,
            output_tokens=0,
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            session_id=self._session_id,
            turn=self._turn,
            duration_ms=duration_ms,
            success=success,
        )

    def _trace_tool(
        self,
        tool_name: str,
        duration_ms: float,
        success: bool,
    ) -> None:
        """Trace a tool call."""
        if self._exporter.is_noop:
            return
        self._exporter.trace_tool_call(
            tool_name=tool_name,
            tenant_id=self._tenant_id,
            session_id=self._session_id,
            duration_ms=duration_ms,
            success=success,
        )
