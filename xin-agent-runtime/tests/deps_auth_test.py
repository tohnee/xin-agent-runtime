# -*- coding: utf-8 -*-
"""G1: get_current_user_id must bind identity to the authenticated
principal, not to a client-supplied ``X-User-ID`` header.

These tests enforce fail-closed identity resolution:

1. When ``AuthMiddleware`` has set ``request.state.principal``, the
   user_id MUST come from the principal (never from the header).
2. When no principal is present (e.g. AuthMiddleware not mounted),
   the ``X-User-ID`` header is accepted as a dev-mode fallback only
   when it is non-empty.
3. When neither principal nor header is present, the request MUST
   be rejected with 401.

The previous implementation trusted any client-supplied
``X-User-ID`` header — a CRITICAL auth bypass allowing identity
spoofing.
"""
# pylint: disable=protected-access
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException, Request

from agentscope.app.deps import get_current_user_id


def _make_request(
    *,
    principal: Any = None,
    headers: dict | None = None,
) -> Any:
    """Build a minimal Request-like object with .state and .headers."""
    state = SimpleNamespace()
    if principal is not None:
        state.principal = principal
    return SimpleNamespace(
        headers=headers or {},
        state=state,
    )


class _FakePrincipal:
    """Minimal principal stand-in mirroring AuthPrincipal.user_id."""

    def __init__(self, user_id: str, tenant_id: str = "acme") -> None:
        self.user_id = user_id
        self.tenant_id = tenant_id


class TestGetCurrentUserIdFailClosed:
    """G1: identity must come from authenticated principal, not
    client-supplied header."""

    async def test_returns_principal_user_id_when_authenticated(
        self,
    ) -> None:
        """When principal is set on request.state, its user_id wins."""
        request = _make_request(
            principal=_FakePrincipal(user_id="alice"),
            headers={"x-user-id": "attacker"},
        )
        user_id = await get_current_user_id(
            request=request,  # type: ignore[arg-type]
            x_user_id="attacker",
        )
        assert (
            user_id == "alice"
        ), "Principal.user_id must take precedence over X-User-ID"

    async def test_ignores_x_user_id_when_principal_present(
        self,
    ) -> None:
        """X-User-ID header must NOT override the principal."""
        request = _make_request(
            principal=_FakePrincipal(user_id="bob"),
            headers={"x-user-id": "attacker"},
        )
        user_id = await get_current_user_id(
            request=request,  # type: ignore[arg-type]
            x_user_id="attacker",
        )
        assert user_id == "bob"

    async def test_fails_closed_when_no_principal_no_header(
        self,
    ) -> None:
        """No principal and no X-User-ID → 401."""
        request = _make_request(principal=None, headers={})
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(
                request=request,  # type: ignore[arg-type]
                x_user_id="",
            )
        assert exc.value.status_code == 401

    async def test_empty_x_user_id_rejected_even_with_no_principal(
        self,
    ) -> None:
        """Empty X-User-ID with no principal → 401."""
        request = _make_request(
            principal=None,
            headers={"x-user-id": ""},
        )
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(
                request=request,  # type: ignore[arg-type]
                x_user_id="",
            )
        assert exc.value.status_code == 401

    async def test_dev_mode_fallback_uses_x_user_id(self) -> None:
        """When no principal is mounted (dev mode), non-empty
        X-User-ID is accepted as fallback."""
        request = _make_request(
            principal=None,
            headers={"x-user-id": "dev-user"},
        )
        user_id = await get_current_user_id(
            request=request,  # type: ignore[arg-type]
            x_user_id="dev-user",
        )
        assert user_id == "dev-user"
