# -*- coding: utf-8 -*-
"""InProcessTarget — assemble build_xruntime_app + fakeredis + MockModel.

The in-process target avoids starting a real HTTP server by using
``httpx.ASGITransport`` to talk directly to the ASGI app.  This keeps
CI fast and port-free.
"""
from __future__ import annotations

import os
from typing import Any


class InProcessTarget:
    """Run evals against an in-process ``build_xruntime_app()``.

    The app is assembled once in :meth:`setup` and reused for all
    evals.  Each eval gets its own ``tenant_id`` + ``session_id``
    (assigned by :class:`EvalRunner`) so there is no cross-eval state
    pollution.
    """

    def __init__(self) -> None:
        self._app: Any = None
        self._ext: dict[str, Any] | None = None
        self._fake_redis: Any = None

    async def setup(self) -> None:
        """Assemble the in-process app with fakeredis + MockModel.

        Raises:
            ImportError: If fakeredis is not installed.
        """
        # Lazy import — fakeredis is a dev-only dep.
        import fakeredis.aioredis  # type: ignore

        from xruntime._server import build_xruntime_app

        self._fake_redis = fakeredis.aioredis.FakeRedis()

        # Inject MockModel via env so ModelResolver picks it up.
        os.environ.setdefault("XRUNTIME_MODEL_PROVIDER", "mock")

        self._app = build_xruntime_app()
        self._ext = getattr(self._app.state, "ext", None) or {}

    async def send(
        self,
        *,
        tenant_id: str,
        role: str,
        message: str,
    ) -> tuple[str, list[dict]]:
        """Send one turn through the ASGI app; return (reply, events).

        Args:
            tenant_id (`str`): The tenant id.
            role (`str`): The user role.
            message (`str`): The user message.

        Returns:
            `tuple[str, list[dict]]`: The reply text and event list.
        """
        import httpx

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app),
            base_url="http://eval",
        ) as client:
            resp = await client.post(
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

    def audit_entries(self, tenant_id: str) -> list[Any]:
        """Read audit log entries for ``tenant_id``.

        Args:
            tenant_id (`str`): The tenant id.

        Returns:
            `list`: Audit entries (may be empty if audit middleware
            is disabled or no entries exist).
        """
        if not self._ext:
            return []
        cache = self._ext.get("middleware_state_cache")
        if cache is None:
            return []
        logger_obj = getattr(cache, "_audit_logger", None)
        if logger_obj is None:
            return []
        return getattr(logger_obj, "entries", [])

    def scan_tenant_keys(self, tenant_id: str) -> list[str]:
        """Scan fakeredis for keys that leak into ``tenant_id``.

        Args:
            tenant_id (`str`): The tenant to scan.

        Returns:
            `list[str]`: Leaked key names (empty if no leak).
        """
        # MVP: fakeredis is in-memory; a full scan is cheap.
        # Real Redis would use SCAN.
        if self._fake_redis is None:
            return []
        import asyncio

        prefix = f"tenant:{tenant_id}:"
        loop = asyncio.get_event_loop()
        keys = loop.run_until_complete(self._fake_redis.keys("*"))
        # Return keys that DON'T belong to this tenant but match a
        # tenant: prefix (cross-tenant leak).
        leaked = []
        for k in keys:
            k_str = k.decode() if isinstance(k, bytes) else str(k)
            if k_str.startswith("tenant:") and not k_str.startswith(prefix):
                leaked.append(k_str)
        return leaked

    def approval_state_snapshot(self, session_id: str) -> set[str]:
        """Read the ApprovalStateCache snapshot for ``session_id``.

        Args:
            session_id (`str`): The session id.

        Returns:
            `set[str]`: Tool names approved in this session.
        """
        if not self._ext:
            return set()
        cache = self._ext.get("middleware_state_cache")
        if cache is None:
            return set()
        approval_cache = getattr(cache, "_approval_state_cache", None)
        if approval_cache is None:
            return set()
        return getattr(approval_cache, "_approved", {}).get(session_id, set())
