# -*- coding: utf-8 -*-
"""Tenant-scope isolation tests for the admin API.

Verifies that an admin principal bound to tenant A cannot list or
search memories belonging to tenant B. The admin endpoints must
honour the principal's ``tenant_id`` and reject cross-tenant
queries with HTTP 403.

Also covers the ``list_available_models`` endpoint's fallback path
when no ``model_router`` is mounted on app.state: the endpoint must
return the default model tiers from :class:`MultiModelRouter`, not
a separately-maintained hardcoded list.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from xruntime._admin_api import _require_admin, admin_router
from xruntime._runtime._tenant._policy import TenantRole
from xruntime._runtime._tenant._store import AuthPrincipal


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeMemory:
    id: str
    content: str
    type: str
    user_id: str
    tenant_id: str
    confidence: float


class _FakeMemoryStore:
    """Records the tenant_id filter the admin endpoint passes through."""

    def __init__(self) -> None:
        self.last_tenant_filter: str | None = None
        self.last_user_filter: str | None = None
        self._memories: list[_FakeMemory] = []

    def add(self, mem: _FakeMemory) -> None:
        self._memories.append(mem)

    def list_all(
        self,
        user_id: str = "",
        tenant_id: str = "default",
    ) -> list[_FakeMemory]:
        self.last_tenant_filter = tenant_id
        self.last_user_filter = user_id
        return [
            m
            for m in self._memories
            if (not tenant_id or m.tenant_id == tenant_id)
            and (not user_id or m.user_id == user_id)
        ]

    def search(
        self,
        query: str,
        user_id: str = "",
        tenant_id: str = "default",
        top_k: int = 10,
    ) -> list[_FakeMemory]:
        self.last_tenant_filter = tenant_id
        self.last_user_filter = user_id
        return [
            m
            for m in self._memories
            if (not tenant_id or m.tenant_id == tenant_id)
            and (not user_id or m.user_id == user_id)
            and query.lower() in m.content.lower()
        ][:top_k]

    @property
    def count(self) -> int:
        return len(self._memories)


class _FakeModelRouter:
    """Stub router exposing ``get_available_models``."""

    def get_available_models(self) -> list[str]:
        return ["router-tier-a", "router-tier-b"]


def _make_app(state: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)
    # ``app.state`` is a SimpleNamespace-like object; assign fields
    # directly so tests can mount fakes.
    for key, value in vars(state).items():
        setattr(app.state, key, value)
    return app


def _make_principal(
    tenant_id: str,
    role: TenantRole = TenantRole.ADMIN,
) -> AuthPrincipal:
    return AuthPrincipal(
        tenant_id=tenant_id,
        user_id="admin-user",
        role=role,
        kb_ids=[],
        api_key_id="test-key",
    )


def _attach_principal(request: Any, principal: AuthPrincipal | None) -> None:
    """Attach a principal to request.state for the dependency to read."""
    request.state.principal = principal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_store_with_two_tenants() -> _FakeMemoryStore:
    store = _FakeMemoryStore()
    store.add(
        _FakeMemory(
            id="acme-1",
            content="acme secret",
            type="long_term",
            user_id="alice",
            tenant_id="acme",
            confidence=0.9,
        ),
    )
    store.add(
        _FakeMemory(
            id="bigco-1",
            content="bigco secret",
            type="long_term",
            user_id="bob",
            tenant_id="bigco",
            confidence=0.9,
        ),
    )
    return store


@pytest.fixture
def app_state(memory_store_with_two_tenants: _FakeMemoryStore):
    """A SimpleNamespace-style app state carrying the fakes."""

    @dataclass
    class _State:
        memory_store: _FakeMemoryStore
        skill_registry: Any = None
        metrics: Any = None
        model_router: Any = None
        middleware_chain: list[Any] = None
        langfuse_enabled: bool = False

    return _State(memory_store=memory_store_with_two_tenants)


# ---------------------------------------------------------------------------
# C3: tenant-scope tests
# ---------------------------------------------------------------------------


class TestRequireAdminTenantScope:
    """C3: ``_require_admin`` must reject cross-tenant admin queries."""

    def test_admin_listing_other_tenant_memories_returns_403(
        self,
        app_state,
        memory_store_with_two_tenants,
    ):
        """Admin of tenant 'acme' must not list memories of 'bigco'."""
        from starlette.requests import Request

        app = _make_app(app_state)
        client = TestClient(app)

        # Bypass real auth middleware by setting principal on each
        # request via a monkey-patch on TestClient. TestClient does not
        # support per-request middleware overrides cleanly, so we use
        # an explicit dependency override that injects the principal
        # captured in a closure.
        acme_principal = _make_principal("acme")

        async def _override_require_admin(request: Request) -> AuthPrincipal:
            _attach_principal(request, acme_principal)
            # Re-run the real check so it can enforce tenant scope:
            return await _require_admin(request)

        app.dependency_overrides[_require_admin] = _override_require_admin

        resp = client.get(
            "/admin/memories",
            params={"tenant_id": "bigco"},
        )

        assert resp.status_code == 403, (
            f"expected 403 for cross-tenant admin query, got "
            f"{resp.status_code}: {resp.text}"
        )
        # The store must NOT have been queried with the victim tenant.
        assert memory_store_with_two_tenants.last_tenant_filter != "bigco"

    def test_admin_searching_other_tenant_memories_returns_403(
        self,
        app_state,
        memory_store_with_two_tenants,
    ):
        """Admin of tenant 'acme' must not search memories of 'bigco'."""
        from starlette.requests import Request

        app = _make_app(app_state)
        client = TestClient(app)

        acme_principal = _make_principal("acme")

        async def _override_require_admin(request: Request) -> AuthPrincipal:
            _attach_principal(request, acme_principal)
            return await _require_admin(request)

        app.dependency_overrides[_require_admin] = _override_require_admin

        resp = client.post(
            "/admin/memories/search",
            json={"query": "secret", "tenant_id": "bigco"},
        )

        assert resp.status_code == 403
        assert memory_store_with_two_tenants.last_tenant_filter != "bigco"

    def test_admin_listing_own_tenant_memories_succeeds(
        self,
        app_state,
        memory_store_with_two_tenants,
    ):
        """Admin of tenant 'acme' listing 'acme' memories must work."""
        from starlette.requests import Request

        app = _make_app(app_state)
        client = TestClient(app)

        acme_principal = _make_principal("acme")

        async def _override_require_admin(request: Request) -> AuthPrincipal:
            _attach_principal(request, acme_principal)
            return await _require_admin(request)

        app.dependency_overrides[_require_admin] = _override_require_admin

        resp = client.get(
            "/admin/memories",
            params={"tenant_id": "acme"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = {m["id"] for m in body["memories"]}
        assert ids == {"acme-1"}, ids
        # No cross-tenant leak.
        assert "bigco-1" not in ids

    def test_admin_without_principal_returns_401(self, app_state):
        """No principal means 401, not 403."""
        from starlette.requests import Request

        app = _make_app(app_state)
        client = TestClient(app)

        async def _override_require_admin(request: Request) -> AuthPrincipal:
            _attach_principal(request, None)
            return await _require_admin(request)

        app.dependency_overrides[_require_admin] = _override_require_admin

        resp = client.get("/admin/memories")
        assert resp.status_code == 401

    def test_non_admin_role_returns_403(self, app_state):
        """Viewer role must not access admin endpoints."""
        from starlette.requests import Request

        app = _make_app(app_state)
        client = TestClient(app)

        viewer_principal = _make_principal(
            "acme",
            role=TenantRole.VIEWER,
        )

        async def _override_require_admin(request: Request) -> AuthPrincipal:
            _attach_principal(request, viewer_principal)
            return await _require_admin(request)

        app.dependency_overrides[_require_admin] = _override_require_admin

        resp = client.get("/admin/memories")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# C4: model list fallback tests
# ---------------------------------------------------------------------------


class TestListAvailableModelsFallback:
    """C4: ``/admin/models`` fallback must come from MultiModelRouter
    defaults, not a separately maintained hardcoded list."""

    def test_fallback_uses_default_tiers(self, app_state):
        """When no model_router is mounted, the endpoint returns the
        default tier models exposed by ``MultiModelRouter``."""
        from starlette.requests import Request

        app = _make_app(app_state)
        client = TestClient(app)

        admin_principal = _make_principal("acme")

        async def _override_require_admin(request: Request) -> AuthPrincipal:
            _attach_principal(request, admin_principal)
            return await _require_admin(request)

        app.dependency_overrides[_require_admin] = _override_require_admin

        resp = client.get("/admin/models")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        from xruntime._runtime._model_router import MultiModelRouter

        expected = list(set(MultiModelRouter().get_available_models()))
        assert set(body["models"]) == set(expected), (
            f"fallback model list drifted from MultiModelRouter defaults: "
            f"{body['models']} != {expected}"
        )

    def test_router_mounted_returns_router_models(self, app_state):
        """When a real router is mounted, its models take precedence."""
        from starlette.requests import Request

        app_state.model_router = _FakeModelRouter()
        app = _make_app(app_state)
        client = TestClient(app)

        admin_principal = _make_principal("acme")

        async def _override_require_admin(request: Request) -> AuthPrincipal:
            _attach_principal(request, admin_principal)
            return await _require_admin(request)

        app.dependency_overrides[_require_admin] = _override_require_admin

        resp = client.get("/admin/models")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body["models"]) == {"router-tier-a", "router-tier-b"}
