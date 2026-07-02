# -*- coding: utf-8 -*-
"""Tenant-aware MessageBus wrapper for multi-tenant isolation.

Wraps an AgentScope ``MessageBus`` so that all Redis keys / channels are
prefixed with ``tenant:{tid}:`` based on the current request's tenant
context (via ``current_tenant`` contextvar).

This mirrors the pattern of :class:`TenantAwareRedisStorage` but for the
message bus — sessions, background tasks, and pub/sub channels from
different tenants never share a Redis key.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Callable

from ._tenant import TenantIsolationError, current_tenant

_logger = logging.getLogger("xruntime.tenant.message_bus")


class TenantAwareMessageBus:
    """Wrapper that prefixes all message-bus keys with the active tenant.

    The wrapper intercepts every method that touches a bus key (session
    events, session locks, inbox, background-task registry, raw
    queue/log/pubsub) and prepends ``tenant:{tid}:`` to the key before
    delegating to the inner bus.

    Usage::

        bus = TenantAwareMessageBus(
            RedisMessageBus(...),
            prefix_template="tenant:{tid}:",
        )

    Args:
        bus:
            The underlying :class:`MessageBus` instance to wrap.
        prefix_template:
            Template for the tenant prefix.  Must contain ``{tid}``.
            Defaults to ``"tenant:{tid}:"``.
    """

    def __init__(
        self,
        bus: Any,
        prefix_template: str = "tenant:{tid}:",
    ) -> None:
        self._bus = bus
        self._prefix_template = prefix_template

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prefix(self) -> str:
        """Return the tenant-specific key prefix for the current context.

        Raises:
            TenantIsolationError: If no tenant is active in context.
        """
        tenant_id = current_tenant.get()
        if not tenant_id:
            raise TenantIsolationError(
                "No tenant in context; cannot resolve message bus prefix. "
                "Set current_tenant before using the message bus.",
            )
        prefix = self._prefix_template.format(tid=tenant_id)
        _logger.debug(
            "Resolved message bus prefix for tenant=%s: %s",
            tenant_id,
            prefix,
        )
        return prefix

    def _k(self, key: str) -> str:
        """Prefix a single key with the current tenant id."""
        return f"{self._prefix()}{key}"

    # ------------------------------------------------------------------
    # Session run coordination
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def session_run(
        self,
        session_id: str,
    ) -> AsyncGenerator[None, None]:
        """Tenant-scoped version of ``MessageBus.session_run``."""
        key = self._k(f"agentscope:session:lock:{session_id}")
        async with self._bus.acquire_lock(key):
            yield

    async def session_is_running(self, session_id: str) -> bool:
        """Tenant-scoped version of ``MessageBus.session_is_running``."""
        key = self._k(f"agentscope:session:lock:{session_id}")
        return await self._bus.is_locked(key)

    async def session_publish_event(
        self,
        session_id: str,
        event: dict,
    ) -> str:
        """Tenant-scoped version of ``MessageBus.session_publish_event``."""
        key = self._k(f"agentscope:session:events:{session_id}")
        entry_id = await self._bus.log_append(
            key,
            event,
            max_len=getattr(self._bus, "_SESSION_REPLAY_MAX_LEN", 1000),
        )
        await self._bus.publish(key, {**event, "_entry_id": entry_id})
        return entry_id

    async def session_read_events(
        self,
        session_id: str,
        since: str | None = None,
        max_count: int = 1000,
    ) -> list[tuple[str, dict]]:
        """Tenant-scoped version of ``MessageBus.session_read_events``."""
        key = self._k(f"agentscope:session:events:{session_id}")
        return await self._bus.log_read(
            key,
            since=since,
            max_count=max_count,
        )

    async def session_subscribe_events(
        self,
        session_id: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Tenant-scoped version of ``MessageBus.session_subscribe_events``."""
        key = self._k(f"agentscope:session:events:{session_id}")
        async for payload in self._bus.subscribe(key, on_ready=on_ready):
            yield {k: v for k, v in payload.items() if k != "_entry_id"}

    async def session_publish_cancel(self, session_id: str) -> None:
        """Tenant-scoped version of ``MessageBus.session_publish_cancel``."""
        key = self._k("agentscope:session:cancel")
        await self._bus.publish(key, {"session_id": session_id})

    async def session_subscribe_cancel(
        self,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Tenant-scoped version of ``MessageBus.session_subscribe_cancel``."""
        key = self._k("agentscope:session:cancel")
        async for payload in self._bus.subscribe(key, on_ready=on_ready):
            sid = payload.get("session_id")
            if isinstance(sid, str):
                yield sid

    async def session_purge(self, session_id: str) -> None:
        """Tenant-scoped version of ``MessageBus.session_purge``."""
        events_key = self._k(f"agentscope:session:events:{session_id}")
        inbox_key = self._k(f"agentscope:session:inbox:{session_id}")
        bg_key = self._k(f"agentscope:bg_tasks:{session_id}")
        await self._bus.log_trim(events_key)
        await self._bus.queue_delete(inbox_key)
        await self._bus.registry_drop(bg_key)

    # ------------------------------------------------------------------
    # Background-task registry
    # ------------------------------------------------------------------

    async def bg_task_register(
        self,
        session_id: str,
        task_id: str,
        metadata: dict,
    ) -> None:
        """Tenant-scoped version of ``MessageBus.bg_task_register``."""
        key = self._k(f"agentscope:bg_tasks:{session_id}")
        await self._bus.registry_set(key, task_id, metadata)

    async def bg_task_unregister(
        self,
        session_id: str,
        task_id: str,
    ) -> None:
        """Tenant-scoped version of ``MessageBus.bg_task_unregister``."""
        key = self._k(f"agentscope:bg_tasks:{session_id}")
        await self._bus.registry_del(key, task_id)

    async def bg_task_exists(
        self,
        session_id: str,
        task_id: str,
    ) -> bool:
        """Tenant-scoped version of ``MessageBus.bg_task_exists``."""
        key = self._k(f"agentscope:bg_tasks:{session_id}")
        return await self._bus.registry_exists(key, task_id)

    async def bg_task_list(
        self,
        session_id: str,
    ) -> dict[str, dict]:
        """Tenant-scoped version of ``MessageBus.bg_task_list``."""
        key = self._k(f"agentscope:bg_tasks:{session_id}")
        return await self._bus.registry_getall(key)

    async def bg_task_purge(self, session_id: str) -> None:
        """Tenant-scoped version of ``MessageBus.bg_task_purge``."""
        key = self._k(f"agentscope:bg_tasks:{session_id}")
        await self._bus.registry_drop(key)

    # ------------------------------------------------------------------
    # Async context manager — delegates to inner bus
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "TenantAwareMessageBus":
        await self._bus.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        return await self._bus.__aexit__(*args)

    # ------------------------------------------------------------------
    # Fallback: fail closed — refuse to forward unknown attributes
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Refuse to forward unknown attributes to the inner bus.

        Previously this hook delegated every unknown attribute to the
        inner bus, which let callers bypass the ``tenant:{tid}:``
        key prefix by invoking ``bus.publish`` / ``bus.acquire_lock``
        / ``bus.log_append`` / ``bus.subscribe`` directly.  Those
        calls would hit the un-prefixed inner-bus key and silently
        break tenant isolation.

        To preserve isolation we now **fail closed**: any public
        attribute that was not explicitly wrapped above raises
        :class:`AttributeError`.  The error message tells the
        developer to add a tenant-prefixed wrapper on this class.

        ``__getattr__`` only fires when normal attribute lookup
        fails, so dunder attributes (``__init__``, ``__repr__``,
        ``__class__``, ...) and underscore-prefixed internal
        attributes set in ``__init__`` (``_bus``,
        ``_prefix_template``, ``_k``, ``_prefix``) remain accessible
        unchanged.  Non-existent underscore-prefixed names raise a
        standard ``AttributeError`` instead of being silently
        forwarded to the inner bus (which would leak its private
        state).

        Args:
            name (`str`): The attribute name being looked up.

        Raises:
            AttributeError: Always, for any attribute that reaches
                this hook.  Public names get a message guiding the
                developer to add a tenant-prefixed wrapper;
                underscore-prefixed names get a standard
                ``AttributeError``.
        """
        if name.startswith("_"):
            # Underscore-prefixed: private/dunder attributes set in
            # __init__ already resolved through __getattribute__.
            # Reaching here means the attribute genuinely does not
            # exist — raise the standard AttributeError instead of
            # forwarding to the inner bus (which would leak its
            # private state and bypass tenant isolation).
            raise AttributeError(
                f"{type(self).__name__!r} has no attribute " f"{name!r}",
            )
        raise AttributeError(
            f"{type(self).__name__!r} does not expose "
            f"{name!r}: forwarding to the inner bus would bypass "
            f"the tenant key prefix. To fix, explicitly wrap "
            f"{name!r} on {type(self).__name__} with a "
            f"tenant-prefixed key (use self._k(...)) and delegate "
            f"to self._bus.{name}(...).",
        )
