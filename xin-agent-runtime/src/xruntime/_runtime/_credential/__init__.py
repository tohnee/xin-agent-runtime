# -*- coding: utf-8 -*-
"""Credential Brokering module — short-lived credentials + broker.

This package implements the credential-brokering boundary mandated
by the Vercel-Eve-style sandbox architecture: long-lived provider
secrets stay on the host; only short-lived, scope/audience-bound
``credential_id`` tokens cross into sandbox containers.

Components:

* :class:`ShortLivedCredential` — a TTL'd wrapper around a provider
  config. Carries ``issued_at`` / ``expires_at`` / ``scopes`` /
  ``audience`` / ``request_id`` so the runtime can validate scope
  before materializing a real :class:`CredentialBase` at the moment
  of use.
* :class:`CredentialBroker` — issues, validates, revokes, and caches
  short-lived credentials. Caches per ``(tenant, session, request)``
  so repeated calls in the same turn reuse the same token.
* :class:`BrokeredModelResolver` — extends :class:`ModelResolver`
  with a ``resolve_with_broker`` method that returns a real
  ``ModelResolution`` backed by a short-lived credential.
* :class:`CredentialBrokerConfig` — pydantic config tree section,
  re-exported from :mod:`xruntime._config` for convenience.
* :class:`RedisCredentialStore` — Redis-backed persistent credential
  store with TTL, multi-tenant key isolation, and session-tuple
  indexing.  Used by the broker to survive restarts and share state
  across gateway replicas.
* :class:`ScopeHierarchy` — DAG-based scope expansion graph.  Lets
  high-level scopes (e.g. ``"admin"``) implicitly grant children
  (e.g. ``"chat"``, ``"embed"``) without enumerating every leaf at
  issue time.  Cycles detected at construction time.
* :class:`AutoRotationPolicy` — decides when a credential is near
  enough to expiry that it should be rotated (remaining TTL <
  threshold).
* :class:`AutoRotationManager` — background sweeper that periodically
  scans the broker cache, finds rotation candidates, revokes + re-
  issues them through the broker, and fires ``on_rotate`` callbacks.
"""
from __future__ import annotations

from ._auto_rotation import AutoRotationManager, AutoRotationPolicy
from ._broker import CredentialBroker
from ._config import CredentialBrokerConfig
from ._model_resolver import BrokeredModelResolver
from ._redis_store import RedisCredentialStore
from ._scope_hierarchy import ScopeHierarchy
from ._short_lived import ShortLivedCredential

__all__ = [
    "ShortLivedCredential",
    "CredentialBroker",
    "BrokeredModelResolver",
    "CredentialBrokerConfig",
    "RedisCredentialStore",
    "ScopeHierarchy",
    "AutoRotationPolicy",
    "AutoRotationManager",
]
