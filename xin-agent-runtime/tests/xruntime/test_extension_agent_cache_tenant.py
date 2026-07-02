# -*- coding: utf-8 -*-
"""Tests for tenant-scoped caching in ``_GatewayState`` and
``_ensure_agent``.

Regression guard for the cross-tenant cache-poisoning bug where the
in-memory ``_agent_cache`` and ``_credential_cache`` were keyed by
``(user_id, agent_name)`` / ``(user_id, provider, api_key)`` without
``tenant_id``. Two tenants sharing the same ``user_id`` (e.g. both
have an "alice") would alias the same cached record — a tenant-A
agent_id would leak into tenant-B's request path even though the
storage layer is properly tenant-prefixed.

Scope:

1. ``_GatewayState.agent_cache`` / ``cache_agent`` must accept
   ``tenant_id`` and isolate by ``(tenant_id, user_id, agent_name)``.
2. ``_GatewayState.credential_id`` / ``cache_credential_id`` must
   accept ``tenant_id`` and isolate by
   ``(tenant_id, user_id, provider, api_key)``.
3. ``_ensure_agent`` must accept ``tenant_id`` and pass it through
   so the cache lookup is tenant-scoped; same-(tenant, user) gets a
   cache hit and different tenants with the same ``user_id`` get
   fresh ``agent_id`` from storage.
"""
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from xruntime._config import XRuntimeConfig
from xruntime._gateway._extension import (
    _GatewayState,
    _ensure_agent,
)
from xruntime._runtime._model_resolver import ModelResolver


def _make_state() -> _GatewayState:
    """Build a ``_GatewayState`` with default config + resolver."""
    return _GatewayState(
        XRuntimeConfig(),
        ModelResolver(),
    )


class TestAgentCacheTenantScoped:
    """``_GatewayState.agent_cache`` must be tenant-scoped."""

    def test_agent_cache_miss_returns_none(self) -> None:
        """Empty cache returns ``None`` for any lookup."""
        state = _make_state()
        assert state.agent_cache("t1", "alice", "agent") is None

    def test_agent_cache_hit_same_tenant_user(self) -> None:
        """Same (tenant, user, agent) hits the cache."""
        state = _make_state()
        state.cache_agent(
            tenant_id="t1",
            user_id="alice",
            agent_name="agent",
            agent_id="aid-1",
            system_prompt="sys",
            max_iters=5,
            model_config_name=None,
        )
        cached = state.agent_cache("t1", "alice", "agent")
        assert cached is not None
        assert cached[0] == "aid-1"

    def test_agent_cache_miss_different_tenant_same_user(
        self,
    ) -> None:
        """Different tenant, same user_id, same agent_name must NOT
        return tenant-A's cached record.
        """
        state = _make_state()
        state.cache_agent(
            tenant_id="t1",
            user_id="alice",
            agent_name="agent",
            agent_id="aid-t1",
            system_prompt="sys",
            max_iters=5,
        )
        # tenant B's alice must NOT alias tenant A's record
        assert state.agent_cache("t2", "alice", "agent") is None

    def test_agent_cache_miss_different_user_same_tenant(
        self,
    ) -> None:
        """Same tenant, different user_id must NOT alias."""
        state = _make_state()
        state.cache_agent(
            tenant_id="t1",
            user_id="alice",
            agent_name="agent",
            agent_id="aid-alice",
            system_prompt="sys",
            max_iters=5,
        )
        assert state.agent_cache("t1", "bob", "agent") is None


class TestCredentialCacheTenantScoped:
    """``_GatewayState.credential_id`` must be tenant-scoped."""

    def test_credential_cache_miss_returns_none(self) -> None:
        """Empty cache returns ``None``."""
        state = _make_state()
        assert state.credential_id("t1", "alice", "openai", "k") is None

    def test_credential_cache_hit_same_tenant(self) -> None:
        """Same (tenant, user, provider, key) hits the cache."""
        state = _make_state()
        state.cache_credential_id(
            tenant_id="t1",
            user_id="alice",
            provider_name="openai",
            api_key="k",
            credential_id="cid-t1",
        )
        assert state.credential_id("t1", "alice", "openai", "k") == "cid-t1"

    def test_credential_cache_miss_different_tenant(
        self,
    ) -> None:
        """Different tenant, same user/provider/key must NOT alias."""
        state = _make_state()
        state.cache_credential_id(
            tenant_id="t1",
            user_id="alice",
            provider_name="openai",
            api_key="k",
            credential_id="cid-t1",
        )
        assert state.credential_id("t2", "alice", "openai", "k") is None


class _FakeStorage:
    """Minimal storage stub that records ``upsert_agent`` calls and
    returns a deterministic, monotonically increasing agent_id per
    call so tests can assert cache-miss vs cache-hit by id.
    """

    def __init__(self) -> None:
        self.upsert_agent_calls: list[tuple[str, Any]] = []
        self._counter = 0

    async def upsert_agent(
        self,
        user_id: str,
        agent_record: Any,
    ) -> str:
        self._counter += 1
        agent_id = f"aid-{self._counter}"
        self.upsert_agent_calls.append((user_id, agent_record))
        return agent_id


class TestEnsureAgentTenantIsolation:
    """``_ensure_agent`` must scope its cache by ``tenant_id``."""

    async def test_cross_tenant_same_user_returns_different_agent_id(
        self,
    ) -> None:
        """tenant-A alice and tenant-B alice must not share an
        ``agent_id`` — second call must miss the cache and ask storage
        for a fresh id.
        """
        state = _make_state()
        storage = _FakeStorage()

        aid_a = await _ensure_agent(
            state,
            storage,
            tenant_id="t1",
            user_id="alice",
            agent_name="agent",
            system_prompt="sys",
            max_iters=5,
        )
        aid_b = await _ensure_agent(
            state,
            storage,
            tenant_id="t2",
            user_id="alice",
            agent_name="agent",
            system_prompt="sys",
            max_iters=5,
        )

        # Two storage writes (cache miss for both tenants)
        assert len(storage.upsert_agent_calls) == 2
        assert aid_a != aid_b
        assert aid_a == "aid-1"
        assert aid_b == "aid-2"

    async def test_same_tenant_user_hits_cache(self) -> None:
        """Same (tenant, user) calling ``_ensure_agent`` twice must
        reuse the cached ``agent_id`` without a second storage write.
        """
        state = _make_state()
        storage = _FakeStorage()

        aid_1 = await _ensure_agent(
            state,
            storage,
            tenant_id="t1",
            user_id="alice",
            agent_name="agent",
            system_prompt="sys",
            max_iters=5,
        )
        aid_2 = await _ensure_agent(
            state,
            storage,
            tenant_id="t1",
            user_id="alice",
            agent_name="agent",
            system_prompt="sys",
            max_iters=5,
        )

        # Cache hit — only one storage write
        assert len(storage.upsert_agent_calls) == 1
        assert aid_1 == aid_2 == "aid-1"

    async def test_cache_poison_regression_after_tenant_a(
        self,
    ) -> None:
        """Regression: after tenant-A populates the cache, a tenant-B
        request with the same ``user_id`` must NOT silently return
        tenant-A's ``agent_id``.
        """
        state = _make_state()
        storage = _FakeStorage()

        aid_a = await _ensure_agent(
            state,
            storage,
            tenant_id="t1",
            user_id="alice",
            agent_name="agent",
            system_prompt="sys",
            max_iters=5,
        )
        # Tenant A second call hits the cache (no new write)
        aid_a_again = await _ensure_agent(
            state,
            storage,
            tenant_id="t1",
            user_id="alice",
            agent_name="agent",
            system_prompt="sys",
            max_iters=5,
        )
        # Tenant B same user_id must NOT return tenant A's id
        aid_b = await _ensure_agent(
            state,
            storage,
            tenant_id="t2",
            user_id="alice",
            agent_name="agent",
            system_prompt="sys",
            max_iters=5,
        )

        assert aid_a == aid_a_again == "aid-1"
        assert aid_b == "aid-2"
        assert aid_b != aid_a
        assert len(storage.upsert_agent_calls) == 2
