# -*- coding: utf-8 -*-
"""Tests for auth principal and membership binding."""

import base64
import json
from types import SimpleNamespace

import pytest

from xruntime._gateway._auth import AuthMiddleware
from xruntime._runtime._tenant import TenantRole
from xruntime._runtime._tenant._store import (
    ApiKeyRecord,
    ApiKeyStore,
    AuthPrincipal,
    JwtClaimsParser,
    TenantMembershipStore,
)

pytestmark = pytest.mark.anyio


def _unsigned_jwt(payload: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def enc(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{enc(header)}.{enc(payload)}."


def test_api_key_resolves_principal() -> None:
    """API key records should resolve authenticated principals."""
    store = ApiKeyStore(
        [
            ApiKeyRecord(
                key="secret",
                tenant_id="acme",
                user_id="alice",
                role=TenantRole.ADMIN,
                kb_ids=["public", "finance"],
            )
        ]
    )

    principal = store.authenticate("secret")

    assert principal == AuthPrincipal(
        tenant_id="acme",
        user_id="alice",
        role=TenantRole.ADMIN,
        kb_ids=["public", "finance"],
        api_key_id="secret",
    )


def test_jwt_claims_resolve_principal() -> None:
    """JWT claims should resolve tenant/user/role/KB scope."""
    token = _unsigned_jwt(
        {
            "tenant_id": "acme",
            "sub": "alice",
            "role": "contributor",
            "kb_ids": ["public"],
        }
    )

    principal = JwtClaimsParser().parse(token)

    assert principal.tenant_id == "acme"
    assert principal.user_id == "alice"
    assert principal.role is TenantRole.CONTRIBUTOR
    assert principal.kb_ids == ["public"]


async def test_auth_middleware_sets_request_principal() -> None:
    """AuthMiddleware should attach the principal to request.state."""
    middleware = AuthMiddleware(
        app=None,
        api_key_store=ApiKeyStore(
            [
                ApiKeyRecord(
                    key="secret",
                    tenant_id="acme",
                    user_id="alice",
                    role=TenantRole.ADMIN,
                )
            ]
        ),
    )
    request = SimpleNamespace(
        url=SimpleNamespace(path="/v1/messages"),
        headers={"x-api-key": "secret", "x-tenant-id": "evil"},
        state=SimpleNamespace(),
    )

    async def call_next(req):
        return req.state.principal

    principal = await middleware.dispatch(request, call_next)

    assert principal.tenant_id == "acme"
    assert principal.user_id == "alice"


def test_header_tenant_cannot_override_principal_tenant() -> None:
    """Tenant is bound to the API key/JWT principal, not x-tenant-id."""
    store = ApiKeyStore(
        [
            ApiKeyRecord(
                key="secret",
                tenant_id="acme",
                user_id="alice",
                role=TenantRole.ADMIN,
            )
        ]
    )
    principal = store.authenticate("secret")

    assert principal.tenant_id == "acme"
    assert principal.tenant_id != "evil"


def test_inactive_member_cannot_authenticate() -> None:
    """Inactive memberships should not produce a usable principal."""
    store = TenantMembershipStore()
    store.upsert(
        tenant_id="acme",
        user_id="alice",
        role=TenantRole.ADMIN,
        status="disabled",
    )

    assert store.resolve_principal("acme", "alice") is None


def test_membership_store_returns_role_per_tenant() -> None:
    """A single user can have different roles in different tenants."""
    store = TenantMembershipStore()
    store.upsert("acme", "alice", TenantRole.ADMIN)
    store.upsert("beta", "alice", TenantRole.VIEWER)

    assert store.resolve_principal("acme", "alice").role is TenantRole.ADMIN
    assert store.resolve_principal("beta", "alice").role is TenantRole.VIEWER


async def test_middleware_factory_assigns_role_from_membership() -> None:
    """The XRuntime factory should assign RBAC role from membership."""
    from xruntime._config import XRuntimeConfig
    from xruntime._gateway._extension import create_xruntime_extension
    from xruntime._runtime._middleware._rbac import RbacMiddleware

    memberships = TenantMembershipStore()
    memberships.upsert("acme", "alice", TenantRole.CONTRIBUTOR)

    ext = create_xruntime_extension(
        config=XRuntimeConfig(),
        tenant_id="acme",
        membership_store=memberships,
    )
    middlewares = await ext["extra_agent_middlewares"](
        "alice",
        "agent-1",
        "sess-1",
    )
    rbac = next(mw for mw in middlewares if isinstance(mw, RbacMiddleware))

    assert rbac.get_role("sess-1") == "contributor"
    assert rbac.check_tool("sess-1", "Write") == "allow"
    assert rbac.check_tool("sess-1", "Bash") == "deny"
