# -*- coding: utf-8 -*-
"""Multi-tenant isolation infrastructure.

AS's ``RedisStorage`` keys are scoped by ``user_id`` only
(``agentscope:user:{user_id}:...``).  XRuntime adds a ``tenant:``
prefix layer to achieve full tenant isolation: every Redis key is
prefixed with ``tenant:{tid}:`` so tenants sharing the same Redis
instance never see each other's data.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Generator


class TenantIsolationError(Exception):
    """Raised when tenant isolation is violated or misconfigured."""


class TenantKeyPrefixer:
    """Applies a tenant-scoped prefix to Redis keys.

    Args:
        prefix_template (`str`):
            Key prefix template containing ``{tid}`` placeholder,
            e.g. ``"tenant:{tid}:"``.
    """

    def __init__(self, prefix_template: str) -> None:
        """Initialize the prefixer."""
        if "{tid}" not in prefix_template:
            raise ValueError(
                "prefix_template must contain '{tid}' placeholder",
            )
        self.prefix_template = prefix_template

    def apply(self, tenant_id: str, key: str) -> str:
        """Prefix a key with the tenant namespace.

        Args:
            tenant_id (`str`):
                The tenant identifier.  Must be non-empty.
            key (`str`):
                The original Redis key.

        Returns:
            `str`: The tenant-scoped key.

        Raises:
            TenantIsolationError: If ``tenant_id`` is empty or None.
        """
        if not tenant_id:
            raise TenantIsolationError(
                "tenant_id must be non-empty for key isolation",
            )
        prefix = self.prefix_template.replace("{tid}", tenant_id)
        return f"{prefix}{key}"


class TenantContext:
    """Context-var-scoped tenant context for request-scoped isolation.

    Uses :class:`contextvars.ContextVar` so the current tenant id is
    correctly isolated across asyncio tasks and concurrent requests.
    Each async task gets its own copy of the context, preventing one
    request's ``set()`` from clobbering another concurrent request's
    tenant id.

    Stores the current tenant id so downstream code (storage, message
    bus, middlewares) can access it without passing it through every
    call.
    """

    _var: contextvars.ContextVar[str]

    def __init__(self, default_tenant: str = "default") -> None:
        """Initialize with a default tenant id.

        Args:
            default_tenant (`str`):
                The tenant id used when none has been set.
        """
        self._default = default_tenant
        self._var = contextvars.ContextVar(
            "xruntime_tenant_id",
            default=default_tenant,
        )

    def set(self, tenant_id: str) -> None:
        """Set the current tenant id.

        Args:
            tenant_id (`str`):
                The tenant id to set.
        """
        self._var.set(tenant_id)

    def get(self) -> str:
        """Get the current tenant id.

        Returns:
            `str`: The current tenant id, or the default if unset.
        """
        return self._var.get()

    def clear(self) -> None:
        """Reset to the default tenant id."""
        self._var.set(self._default)

    @contextmanager
    def tenant(self, tenant_id: str) -> Generator[None, None, None]:
        """Context manager that temporarily sets the tenant.

        Uses a :class:`contextvars.Token` to restore the previous
        value on exit, ensuring correct behavior even in nested
        async contexts.

        Args:
            tenant_id (`str`):
                The tenant id to use within the context.

        Yields:
            `None`
        """
        token = self._var.set(tenant_id)
        try:
            yield
        finally:
            self._var.reset(token)


# Process-wide tenant context. Request handlers set it from the
# ``x-tenant-id`` header so downstream code can read the active tenant
# without threading it through every call.
current_tenant = TenantContext()


def build_tenant_key_config(
    tenant_id: str,
    prefix_template: str,
    base_key_config: Any = None,
) -> Any:
    """Build a tenant-prefixed :class:`RedisStorage.KeyConfig`.

    Every key template in the config is prefixed with the resolved
    tenant namespace (``prefix_template`` with ``{tid}`` substituted),
    so two tenants sharing one Redis instance never collide on keys —
    implementing the full key-prefix isolation described in the design
    doc.

    Args:
        tenant_id (`str`):
            The tenant whose namespace to apply. Must be non-empty.
        prefix_template (`str`):
            The prefix template containing ``{tid}`` (e.g.
            ``"tenant:{tid}:"``).
        base_key_config (`RedisStorage.KeyConfig | None`):
            A base config to prefix. ``None`` uses a fresh default.

    Returns:
        `RedisStorage.KeyConfig`: A new config with every template
        prefixed by the tenant namespace.

    Raises:
        TenantIsolationError: If ``tenant_id`` is empty.
    """
    if not tenant_id:
        raise TenantIsolationError(
            "tenant_id must be non-empty for key isolation",
        )

    from agentscope.app.storage import RedisStorage

    prefixer = TenantKeyPrefixer(prefix_template)
    base = base_key_config or RedisStorage.KeyConfig()
    prefixed = {
        field: prefixer.apply(tenant_id, value)
        for field, value in base.model_dump().items()
    }
    return RedisStorage.KeyConfig(**prefixed)
