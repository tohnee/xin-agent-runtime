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

    _var: contextvars.ContextVar[str | None]

    def __init__(self) -> None:
        """Initialize with no default tenant (None means unset)."""
        self._var = contextvars.ContextVar(
            "xruntime_tenant_id",
            default=None,
        )

    def set(self, tenant_id: str) -> contextvars.Token:
        """Set the current tenant id.

        Args:
            tenant_id (`str`):
                The tenant id to set.

        Returns:
            `contextvars.Token`: Token for resetting later.
        """
        return self._var.set(tenant_id)

    def get(self) -> str | None:
        """Get the current tenant id.

        Returns:
            `str | None`: The current tenant id, or None if unset.
        """
        return self._var.get()

    def reset(self, token: contextvars.Token) -> None:
        """Reset to a previous value using a token from set().

        Args:
            token (`contextvars.Token`):
                Token returned by a previous set() call.
        """
        self._var.reset(token)

    def clear(self) -> None:
        """Reset to unset state."""
        self._var.set(None)

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


# Process-wide tenant context. Auth middleware sets it from the
# authenticated principal so downstream code can read the active tenant
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
