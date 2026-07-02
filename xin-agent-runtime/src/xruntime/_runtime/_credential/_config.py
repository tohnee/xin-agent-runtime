# -*- coding: utf-8 -*-
"""CredentialBrokerConfig — pydantic config for the credential broker.

Re-exported from :mod:`xruntime._config` as
``XRuntimeConfig.credential_broker`` so it can be set in YAML or via
``XRUNTIME_CREDENTIAL_BROKER_*`` env vars.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CredentialBrokerConfig(BaseModel):
    """Configuration for the credential broker.

    Args:
        enabled (`bool`):
            Whether short-lived credential brokering is active.
            When ``False``, the runtime uses long-lived credentials
            directly (the default — backward compatible).
        default_ttl_seconds (`int`):
            Default TTL for issued credentials, in seconds.
        max_ttl_seconds (`int`):
            Maximum TTL any single credential can have. Requests
            asking for a longer TTL are clamped to this value.
        default_scopes (`list[str]`):
            Scopes granted to every issued credential unless
            overridden at issue-time.
        allowed_scopes (`list[str]`):
            Scope allowlist.  When non-empty, ``issue()`` /
            ``issue_for_session()`` / ``issue_for_workspace()`` reject
            any request whose effective scopes (explicit or defaulted)
            are not a subset of this list.  Empty (default) means no
            enforcement — backward compatible.
        cache_max_size (`int`):
            Maximum number of cached credentials before LRU eviction.
        redis_url (`str | None`):
            Redis connection URL for persistent credential storage.
            When ``None`` (default), the broker uses in-memory
            caching only.  When set, a :class:`RedisCredentialStore`
            is wired in so credentials survive broker restarts and
            can be shared across gateway replicas.
        redis_key_prefix (`str`):
            Redis key prefix with ``{tid}`` placeholder for tenant
            id.  Only used when ``redis_url`` is set.
        auto_rotate_enabled (`bool`):
            Whether proactive credential rotation is active.  When
            ``True``, an :class:`AutoRotationManager` is started
            alongside the broker to refresh near-expiry credentials
            before they actually expire.
        auto_rotate_threshold_seconds (`int`):
            Remaining TTL below which a credential is rotated by the
            auto-rotation manager.  Only used when
            ``auto_rotate_enabled`` is ``True``.
        auto_rotate_check_interval_seconds (`float`):
            Background sweep interval in seconds.  Only used when
            ``auto_rotate_enabled`` is ``True``.
        scope_hierarchy (`dict[str, list[str]]`):
            Mapping from a scope to the list of scopes it implicitly
            grants.  When non-empty, a :class:`ScopeHierarchy` is
            constructed and used by the broker to expand scopes at
            issue time and validate required scopes at use time.
            Empty (default) means no hierarchy — flat scope space.
    """

    enabled: bool = False
    default_ttl_seconds: int = 3600
    max_ttl_seconds: int = 86400
    default_scopes: list[str] = Field(default_factory=list)
    allowed_scopes: list[str] = Field(default_factory=list)
    cache_max_size: int = 1000
    redis_url: str | None = None
    redis_key_prefix: str = "tenant:{tid}:creds:"
    auto_rotate_enabled: bool = False
    auto_rotate_threshold_seconds: int = 300
    auto_rotate_check_interval_seconds: float = 60.0
    scope_hierarchy: dict[str, list[str]] = Field(default_factory=dict)
