# -*- coding: utf-8 -*-
"""RemoteTarget — talk to a running XRuntime server over HTTP.

Used by nightly / staging evals where a real model is wired.  The
remote target does NOT assemble an app — it just sends HTTP requests
to ``XRUNTIME_EVAL_TARGET``.
"""
from __future__ import annotations

from typing import Any


class RemoteTarget:
    """Run evals against a remote XRuntime server.

    Args:
        base_url (`str`): The server URL (e.g. ``http://localhost:8900``).
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client: Any = None

    async def setup(self) -> None:
        """Initialize the HTTP client."""
        import httpx

        self._client = httpx.AsyncClient(base_url=self.base_url)

    async def teardown(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(
        self,
        *,
        tenant_id: str,
        role: str,
        message: str,
    ) -> tuple[str, list[dict]]:
        """Send one turn to the remote server.

        Args:
            tenant_id (`str`): The tenant id.
            role (`str`): The user role.
            message (`str`): The user message.

        Returns:
            `tuple[str, list[dict]]`: The reply text and event list.
        """
        resp = await self._client.post(
            "/v1/chat",
            json={
                "tenant_id": tenant_id,
                "role": role,
                "message": message,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("reply", ""), data.get("events", [])

    def audit_entries(self, _tenant_id: str) -> list[Any]:
        """Not available on remote target (no MiddlewareStateCache access).

        Returns:
            `list`: Always empty — use the audit API endpoint instead
            (Phase 3).
        """
        return []

    def scan_tenant_keys(self, _tenant_id: str) -> list[str]:
        """Not available on remote target.

        Returns:
            `list`: Always empty.
        """
        return []

    def approval_state_snapshot(self, _session_id: str) -> set[str]:
        """Not available on remote target.

        Returns:
            `set`: Always empty.
        """
        return set()
