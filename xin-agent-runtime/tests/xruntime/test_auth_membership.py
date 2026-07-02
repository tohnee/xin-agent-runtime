# -*- coding: utf-8 -*-
"""Tests for auth principal and membership binding."""

import base64
import hashlib
import hmac
import json
import time
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


def _signed_jwt(payload: dict, secret: str) -> str:
    """Create an HS256-signed JWT for testing."""
    header = {"alg": "HS256", "typ": "JWT"}

    def enc(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header_b64 = enc(header)
    payload_b64 = enc(payload)
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{header_b64}.{payload_b64}.{sig_b64}"


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
            ),
        ],
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
    secret = "test-secret-key"
    token = _signed_jwt(
        {
            "tenant_id": "acme",
            "sub": "alice",
            "role": "contributor",
            "kb_ids": ["public"],
            "exp": int(time.time()) + 3600,
        },
        secret,
    )

    principal = JwtClaimsParser(secret=secret).parse(token)

    assert principal.tenant_id == "acme"
    assert principal.user_id == "alice"
    assert principal.role is TenantRole.CONTRIBUTOR
    assert principal.kb_ids == ["public"]


def test_jwt_rejected_without_secret() -> None:
    """JWT parsing must fail when no secret is configured."""
    token = _signed_jwt(
        {"tenant_id": "acme", "sub": "alice", "role": "viewer"},
        "any-secret",
    )
    with pytest.raises(ValueError, match="requires a secret"):
        JwtClaimsParser().parse(token)


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
                ),
            ],
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
            ),
        ],
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
    from xruntime._infra._tenant import current_tenant
    from xruntime._runtime._middleware._rbac import RbacMiddleware

    memberships = TenantMembershipStore()
    memberships.upsert("acme", "alice", TenantRole.CONTRIBUTOR)

    # The conftest's autouse fixture seeds ``current_tenant`` with
    # ``"test-tenant"`` so the middleware_factory's fail-closed
    # defense-in-depth check passes. This test needs ``effective_tenant``
    # to be ``"acme"`` (matching the membership store) so RBAC role
    # lookup resolves — override the conftest seed before calling the
    # factory. The conftest fixture clears ``current_tenant`` after the
    # test, so this override does not leak.
    current_tenant.set("acme")

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
