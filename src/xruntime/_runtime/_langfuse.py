# -*- coding: utf-8 -*-
"""Langfuse observability integration (M7).

Provides an optional Langfuse exporter for LLM traces. When disabled
or when the ``langfuse`` package is not installed, a NoopExporter is
used so the main flow is never affected.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel


class LangfuseConfig(BaseModel):
    """Langfuse configuration.

    Args:
        enabled (`bool`):
            Whether Langfuse tracing is enabled.
        host (`str`):
            Langfuse server URL.
        public_key (`str`):
            Public key for auth.
        secret_key (`str`):
            Secret key for auth.
    """

    enabled: bool = False
    host: str = ""
    public_key: str = ""
    secret_key: str = ""


def _redact_payload(payload: Any) -> Any:
    """Redact secrets from a trace payload.

    Args:
        payload (`Any`): The payload to redact.

    Returns:
        `Any`: A copy with secrets replaced by ``[REDACTED_*]``.
    """
    if isinstance(payload, str):
        result = payload
        result = re.sub(
            r"sk-[a-zA-Z0-9]{20,}",
            "[REDACTED_API_KEY]",
            result,
        )
        result = re.sub(
            r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*",
            "Bearer [REDACTED_TOKEN]",
            result,
        )
        return result
    if isinstance(payload, dict):
        return {k: _redact_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_redact_payload(v) for v in payload]
    return payload


class LangfuseExporter:
    """Langfuse trace exporter with Noop fallback.

    Args:
        config (`LangfuseConfig`):
            The Langfuse configuration.
    """

    def __init__(self, config: LangfuseConfig) -> None:
        """Initialize the exporter."""
        self._config = config
        self._client: Any = None
        self._noop = True

        if config.enabled and config.public_key and config.secret_key:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(
                    host=config.host,
                    public_key=config.public_key,
                    secret_key=config.secret_key,
                )
                self._noop = False
            except ImportError:
                pass

    @property
    def is_noop(self) -> bool:
        """Whether this exporter is a no-op."""
        return self._noop

    def trace_generation(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        tenant_id: str = "",
        user_id: str = "",
        session_id: str = "",
        **extra: Any,
    ) -> None:
        """Trace a model generation.

        Args:
            model (`str`): Model name.
            input_tokens (`int`): Input token count.
            output_tokens (`int`): Output token count.
            tenant_id (`str`): Tenant scope.
            user_id (`str`): User scope.
            session_id (`str`): Session scope.
            **extra: Additional metadata.
        """
        if self._noop:
            return
        self._client.generation(
            name=f"model:{model}",
            model=model,
            usage={
                "input": input_tokens,
                "output": output_tokens,
            },
            metadata=_redact_payload(
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    **extra,
                },
            ),
        )

    def trace_tool_call(
        self,
        tool_name: str,
        tenant_id: str = "",
        **extra: Any,
    ) -> None:
        """Trace a tool call.

        Args:
            tool_name (`str`): Tool name.
            tenant_id (`str`): Tenant scope.
            **extra: Additional metadata.
        """
        if self._noop:
            return
        self._client.span(
            name=f"tool:{tool_name}",
            metadata=_redact_payload(
                {"tenant_id": tenant_id, **extra},
            ),
        )

    def trace_knowledge_retrieve(
        self,
        query: str,
        results: int,
        tenant_id: str = "",
        **extra: Any,
    ) -> None:
        """Trace a knowledge retrieval.

        Args:
            query (`str`): The retrieval query.
            results (`int`): Number of results.
            tenant_id (`str`): Tenant scope.
            **extra: Additional metadata.
        """
        if self._noop:
            return
        self._client.span(
            name="knowledge:retrieve",
            metadata=_redact_payload(
                {
                    "tenant_id": tenant_id,
                    "query": query,
                    "results": results,
                    **extra,
                },
            ),
        )
