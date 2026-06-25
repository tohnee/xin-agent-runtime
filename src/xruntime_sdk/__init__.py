# -*- coding: utf-8 -*-
"""XRuntime SDK — unified client for all three protocols.

Provides a single :class:`XRuntimeClient` that can drive agent
sessions via any of the three supported protocols (Anthropic,
Claude Code, OpenCode), plus an :class:`AdminClient` for
management operations.
"""
from __future__ import annotations

from typing import Any

from httpx import AsyncClient


class XRuntimeClient:
    """Unified SDK client for XRuntime.

    Args:
        base_url (`str`):
            XRuntime server base URL (e.g. ``"http://localhost:8900"``).
        tenant_id (`str`):
            Tenant identifier for multi-tenant isolation.
        api_key (`str | None`):
            API key for authentication.
        user_id (`str`):
            User identifier.
    """

    def __init__(
        self,
        base_url: str,
        tenant_id: str = "default",
        api_key: str | None = None,
        user_id: str = "anonymous",
    ) -> None:
        """Initialize the client."""
        self.base_url = base_url
        self.tenant_id = tenant_id
        self.api_key = api_key
        self.user_id = user_id
        self._client: AsyncClient = AsyncClient(base_url=base_url)

    def _headers(self) -> dict[str, str]:
        """Build request headers with tenant/auth info.

        Returns:
            `dict[str, str]`: Headers dict.
        """
        h: dict[str, str] = {
            "x-tenant-id": self.tenant_id,
            "x-user-id": self.user_id,
        }
        if self.api_key:
            h["x-api-key"] = self.api_key
        return h

    async def health(self) -> dict[str, Any]:
        """Check server health.

        Returns:
            `dict[str, Any]`: Health status dict.
        """
        resp = await self._client.get("/health")
        return resp.json()

    async def ready(self) -> dict[str, Any]:
        """Check server readiness.

        Returns:
            `dict[str, Any]`: Readiness status dict.
        """
        resp = await self._client.get("/ready")
        return resp.json()

    async def query(
        self,
        protocol: str,
        prompt: str,
        *,
        model: str = "",
        options: dict[str, Any] | None = None,
        session_id: str | None = None,
        agent: str = "",
        config: dict[str, Any] | None = None,
    ) -> list[str]:
        """Send a query to the XRuntime server.

        Args:
            protocol (`str`):
                Protocol — ``"anthropic"``, ``"claude_code"``,
                or ``"opencode"``.
            prompt (`str`):
                The user prompt.
            model (`str`):
                Model name (Anthropic protocol).
            options (`dict | None`):
                Claude Code ``ClaudeAgentOptions`` fields.
            session_id (`str | None`):
                Session to resume.
            agent (`str`):
                Agent name (OpenCode protocol).
            config (`dict | None`):
                Inline OpenCode config.

        Returns:
            `list[str]`: List of NDJSON response lines (each a
            JSON-encoded event/message).
        """
        route_map = {
            "anthropic": "/v1/messages",
            "claude_code": "/v1/claude-code/query",
            "opencode": "/v1/opencode",
        }
        route = route_map.get(protocol, "/v1/messages")

        if protocol == "anthropic":
            body: dict[str, Any] = {
                "model": model or "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
            }
        elif protocol == "claude_code":
            body = {
                "prompt": prompt,
                "options": options or {},
            }
        else:
            body = {
                "prompt": prompt,
                "agent": agent,
                "config": config or {},
            }

        headers = self._headers()
        if session_id:
            headers["x-session-id"] = session_id

        resp = await self._client.post(
            route,
            json=body,
            headers=headers,
        )
        lines = resp.text.strip().split("\n")
        return [line for line in lines if line]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


class AdminClient:
    """Admin management client for XRuntime.

    Args:
        base_url (`str`):
            XRuntime server base URL.
        api_key (`str | None`):
            Admin API key.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
    ) -> None:
        """Initialize the admin client."""
        self.base_url = base_url
        self.api_key = api_key
        self._client: AsyncClient = AsyncClient(base_url=base_url)

    async def server_info(self) -> dict[str, Any]:
        """Get server health/info.

        Returns:
            `dict[str, Any]`: Server info dict.
        """
        resp = await self._client.get("/health")
        return resp.json()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


def create_client(
    base_url: str,
    tenant_id: str = "default",
    api_key: str | None = None,
) -> XRuntimeClient:
    """Factory function to create an :class:`XRuntimeClient`.

    Args:
        base_url (`str`):
            XRuntime server base URL.
        tenant_id (`str`):
            Tenant identifier.
        api_key (`str | None`):
            API key for auth.

    Returns:
        `XRuntimeClient`: A new client instance.
    """
    return XRuntimeClient(
        base_url=base_url,
        tenant_id=tenant_id,
        api_key=api_key,
    )
