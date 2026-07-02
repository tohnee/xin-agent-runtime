# -*- coding: utf-8 -*-
"""Tests for tenant fail-closed + anti-spoofing in the gateway handler.

Covers the two related security bugs in
:mod:`xruntime._gateway._extension`:

1. ``middleware_factory`` (~line 239) resolves ``effective_tenant``
   with ``current_tenant.get() or tenant_id or "default"`` which is
   fail-open: when auth is enabled but the auth middleware never set
   the contextvar, the request silently falls back to ``"default"``
   — a cross-tenant leak vector.

2. The gateway handler (~lines 726-747) has an anti-spoofing comment
   but no actual comparison between ``principal.tenant_id`` and
   ``xrt_request.tenant_id``. A client can send any ``x-tenant-id``
   header value and the handler will silently use it (or, after a
   naive fix, silently override it without rejecting the spoof).

Driven via httpx ASGI transport against a minimal FastAPI app that
mounts only the Anthropic adapter. The downstream materialization is
short-circuited with a marker ``_MaterializeError`` so success
scenarios prove the principal check passed (rather than exercising
the full AS ChatService stack).
"""
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from xruntime._config import XRuntimeConfig
from xruntime._gateway._extension import (
    _MaterializeError,
    _default_adapters,
    mount_protocol_adapters,
)
from xruntime._infra._tenant import current_tenant
from xruntime._runtime._model_resolver import ModelResolver
from xruntime._runtime._tenant import AuthPrincipal, TenantRole


# Marker raised by the patched ``_materialize_session`` so a test can
# distinguish "principal check passed and we reached materialize"
# (this marker) from "rejected at the principal check" (401 / 403).
_MARKER_OK = "marker_principal_check_passed"


class _PrincipalInjectMiddleware(BaseHTTPMiddleware):
    """Test middleware that sets ``request.state.principal``.

    Replaces :class:`AuthMiddleware` in tests so we can drive the
    gateway handler's principal-resolution branch directly. Pass
    ``principal=None`` to install a passthrough middleware (no
    principal set).
    """

    def __init__(self, app: Any, principal: Any) -> None:
        """Initialize the middleware.

        Args:
            app (`Any`): The wrapped ASGI app.
            principal (`Any`): The principal to inject, or ``None``
                to leave ``request.state.principal`` unset.
        """
        super().__init__(app)
        self._principal = principal

    async def dispatch(self, request: Any, call_next: Any) -> Any:
        """Inject principal before calling the next handler."""
        if self._principal is not None:
            request.state.principal = self._principal
        return await call_next(request)


def _patch_materialize(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``_materialize_session`` to raise a marker error.

    Lets success scenarios assert the request reached the
    materialization step (proving the principal check passed) without
    standing up the full AS ChatService stack.

    Args:
        monkeypatch (`pytest.MonkeyPatch`): The pytest monkeypatch
            fixture.
    """

    async def _fake_materialize(*_args: Any, **_kwargs: Any) -> Any:
        raise _MaterializeError(_MARKER_OK, status_code=422)

    monkeypatch.setattr(
        "xruntime._gateway._extension._materialize_session",
        _fake_materialize,
    )


def _make_principal(tenant_id: str = "tenant-A") -> AuthPrincipal:
    """Build a minimal ``AuthPrincipal`` for tests.

    Args:
        tenant_id (`str`): The tenant id to bind the principal to.

    Returns:
        `AuthPrincipal`: A viewer-role principal for ``tenant_id``.
    """
    return AuthPrincipal(
        tenant_id=tenant_id,
        user_id="user-A",
        role=TenantRole.VIEWER,
    )


def _make_app(
    *,
    auth_enabled: bool,
    principal: Any,
) -> FastAPI:
    """Build a minimal FastAPI app with the Anthropic adapter mounted.

    Sets mocked ``chat_service`` / ``chat_run_registry`` / ``storage``
    / ``message_bus`` on ``app.state`` so the handler can read them
    without raising before the principal check. The principal-inject
    middleware is added only when ``principal`` is not ``None``.

    Args:
        auth_enabled (`bool`): Value of ``config.server.auth_enabled``
            — drives the handler's fail-closed branch.
        principal (`Any`): Principal to inject, or ``None`` to leave
            ``request.state.principal`` unset.

    Returns:
        `FastAPI`: The configured app (lifespan not yet started).
    """
    config = XRuntimeConfig()
    config.server.auth_enabled = auth_enabled

    app = FastAPI()
    app.state.chat_service = MagicMock()
    app.state.chat_run_registry = MagicMock()
    app.state.storage = MagicMock()
    app.state.message_bus = MagicMock()

    registry = _default_adapters()
    mount_protocol_adapters(
        app,
        registry,
        config=config,
        model_resolver=ModelResolver(),
    )

    # Always install the inject middleware so the route is consistent.
    # When ``principal`` is None it acts as a passthrough.
    app.add_middleware(_PrincipalInjectMiddleware, principal=principal)

    return app


def _anthropic_body() -> dict:
    """Build a minimal Anthropic /v1/messages request body."""
    return {
        "model": "mock",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1024,
    }


@pytest.fixture(autouse=True)
def _clear_tenant_ctx() -> None:
    """Clear the process-wide ``current_tenant`` contextvar.

    Prevents a leftover tenant id from a previous test (or from the
    handler itself if it crashed before its finally block) from
    leaking into the next test's assertions.
    """
    current_tenant.clear()
    yield
    current_tenant.clear()


class TestTenantFailClosed:
    """Verify gateway handler enforces tenant fail-closed + anti-spoofing."""

    async def test_auth_enabled_principal_set_uses_principal_tenant(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auth enabled + principal set + matching tenant → proceeds.

        The request reaches ``_materialize_session`` (marker 422),
        proving the principal check passed and the spoof check did
        not fire because ``xrt_request.tenant_id == principal.tenant_id``.
        """
        _patch_materialize(monkeypatch)
        principal = _make_principal(tenant_id="tenant-A")
        app = _make_app(auth_enabled=True, principal=principal)
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/v1/messages",
                    json=_anthropic_body(),
                    headers={"x-tenant-id": "tenant-A"},
                )
        # Materialize was reached → principal check passed.
        assert response.status_code == 422
        assert _MARKER_OK in response.text

    async def test_auth_enabled_principal_set_client_spoofs_tenant_id_rejected_403(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auth enabled + principal set + spoofed x-tenant-id → 403.

        Client claims ``tenant-B`` in the header but the principal
        is bound to ``tenant-A``. The handler must reject with 403
        instead of silently overriding or trusting the spoofed value.
        """
        _patch_materialize(monkeypatch)
        principal = _make_principal(tenant_id="tenant-A")
        app = _make_app(auth_enabled=True, principal=principal)
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/v1/messages",
                    json=_anthropic_body(),
                    headers={"x-tenant-id": "tenant-B"},
                )
        assert response.status_code == 403

    async def test_auth_enabled_principal_none_rejects_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auth enabled + no principal (misconfig) → 401 fail-closed.

        Defense-in-depth: if auth is enabled but no principal was set
        on ``request.state`` (e.g. AuthMiddleware not mounted due to a
        misconfiguration, or a middleware ordering bug), the handler
        must reject with 401 rather than falling back to ``"default"``.
        """
        _patch_materialize(monkeypatch)
        app = _make_app(auth_enabled=True, principal=None)
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/v1/messages",
                    json=_anthropic_body(),
                    headers={"x-tenant-id": "default"},
                )
        assert response.status_code == 401

    async def test_auth_disabled_uses_request_tenant_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auth disabled + no principal → uses x-tenant-id (backwards compat).

        When auth is disabled (e.g. local dev / single-tenant embed),
        the handler trusts the client-supplied ``x-tenant-id`` and
        proceeds. This preserves the pre-fix backwards-compatible
        behavior so existing embeds are not broken.
        """
        _patch_materialize(monkeypatch)
        app = _make_app(auth_enabled=False, principal=None)
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/v1/messages",
                    json=_anthropic_body(),
                    headers={"x-tenant-id": "tenant-X"},
                )
        # Materialize was reached → backwards-compat path works.
        assert response.status_code == 422
        assert _MARKER_OK in response.text
