# -*- coding: utf-8 -*-
"""Tenant-aware Redis storage wrapper for per-request multi-tenant isolation.

Wraps :class:`agentscope.app.storage.RedisStorage` so that the key prefix is
resolved dynamically from :data:`current_tenant` on every operation, instead
of being fixed at app-start time to a single tenant.

This implements the P0 fix for "RedisStorage key_config 启动时固定 tenant，
不是真正 per-request 多租户隔离" from the 0630 review.
"""
from __future__ import annotations

import logging
from typing import Any

from xruntime._infra._tenant import (
    TenantIsolationError,
    build_tenant_key_config,
    current_tenant,
)

_logger = logging.getLogger("xruntime.tenant.storage")


class TenantAwareRedisStorage:
    """A RedisStorage wrapper that resolves the tenant prefix per-request.

    Instead of baking a single tenant's key prefix into the storage at
    startup, this wrapper reads :data:`current_tenant` on every attribute
    access and re-derives the ``key_config`` for the active tenant.  All
    other methods are delegated to the underlying :class:`RedisStorage`.

    Args:
        storage (`RedisStorage`):
            The underlying storage instance to wrap.
        prefix_template (`str`):
            The tenant-prefix template containing ``{tid}``,
            e.g. ``"tenant:{tid}:"``.
    """

    def __init__(
        self,
        storage: Any,
        prefix_template: str,
    ) -> None:
        """Initialize the tenant-aware wrapper."""
        self._storage = storage
        self._prefix_template = prefix_template
        self._base_key_config = storage.key_config

    def __getattr__(self, name: str) -> Any:
        """Delegate all unknown attributes to the underlying storage.

        Special-cases ``key_config`` so it is recomputed from the current
        tenant on each access.
        """
        if name == "key_config":
            return self._resolve_key_config()
        return getattr(self._storage, name)

    def _resolve_key_config(self) -> Any:
        """Build a key_config for the currently active tenant.

        Returns:
            `RedisStorage.KeyConfig`: A per-request key config with the
            current tenant's prefix applied.

        Raises:
            TenantIsolationError: If ``current_tenant`` is not set.
        """
        tenant_id = current_tenant.get()
        if not tenant_id:
            raise TenantIsolationError(
                "No current tenant set; cannot resolve storage key prefix. "
                "Ensure the auth middleware has set current_tenant.",
            )
        _logger.debug(
            "Resolving storage key_config for tenant=%s, prefix_template=%s",
            tenant_id,
            self._prefix_template,
        )
        return build_tenant_key_config(
            tenant_id,
            self._prefix_template,
            self._base_key_config,
        )

    # ---- Explicitly delegated methods for clarity and type safety ----

    async def __aenter__(self) -> "TenantAwareRedisStorage":
        await self._storage.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        return await self._storage.__aexit__(*args)

    async def aclose(self) -> None:
        await self._storage.aclose()

    def get_client(self) -> Any:
        return self._storage.get_client()

    # ---- Storage operations (delegated, key_config is resolved lazily) ----

    async def upsert_credential(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.upsert_credential(*args, **kwargs)

    async def list_credentials(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.list_credentials(*args, **kwargs)

    async def get_credential(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.get_credential(*args, **kwargs)

    async def delete_credential(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.delete_credential(*args, **kwargs)

    async def upsert_agent(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.upsert_agent(*args, **kwargs)

    async def list_agents(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.list_agents(*args, **kwargs)

    async def get_agent(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.get_agent(*args, **kwargs)

    async def delete_agent(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.delete_agent(*args, **kwargs)

    async def upsert_session(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.upsert_session(*args, **kwargs)

    async def update_session_state(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.update_session_state(*args, **kwargs)

    async def list_sessions(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.list_sessions(*args, **kwargs)

    async def get_session(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.get_session(*args, **kwargs)

    async def delete_session(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.delete_session(*args, **kwargs)

    async def upsert_message(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.upsert_message(*args, **kwargs)

    async def get_message(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.get_message(*args, **kwargs)

    async def list_messages(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.list_messages(*args, **kwargs)

    async def upsert_schedule(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.upsert_schedule(*args, **kwargs)

    async def get_schedule(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.get_schedule(*args, **kwargs)

    async def list_schedules(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.list_schedules(*args, **kwargs)

    async def delete_schedule(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.delete_schedule(*args, **kwargs)

    async def list_sessions_by_schedule(
        self, *args: Any, **kwargs: Any
    ) -> Any:
        return await self._storage.list_sessions_by_schedule(*args, **kwargs)

    async def list_all_schedules(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.list_all_schedules(*args, **kwargs)

    async def upsert_team(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.upsert_team(*args, **kwargs)

    async def get_team(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.get_team(*args, **kwargs)

    async def list_teams(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.list_teams(*args, **kwargs)

    async def set_session_team_id(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.set_session_team_id(*args, **kwargs)

    async def delete_team(self, *args: Any, **kwargs: Any) -> Any:
        return await self._storage.delete_team(*args, **kwargs)
