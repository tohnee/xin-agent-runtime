# -*- coding: utf-8 -*-
"""Gateway authentication middleware — API Key / JWT."""
from __future__ import annotations

from typing import Any

from .._runtime._tenant._store import (
    ApiKeyStore,
    AuthPrincipal,
    JwtClaimsParser,
)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


# Routes that bypass authentication
_PUBLIC_ROUTES: set[str] = {
    "/health",
    "/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that enforces API key authentication.

    Requests to public routes (``/health``, ``/ready``, ``/docs``)
    bypass auth.  All other routes require a valid ``x-api-key``
    header matching one of the configured keys.

    Args:
        app (`Any`):
            The ASGI app to wrap.
        api_keys (`set[str]`):
            Set of valid API keys.
    """

    def __init__(
        self,
        app: Any,
        api_keys: set[str] | None = None,
        api_key_store: ApiKeyStore | None = None,
        jwt_parser: JwtClaimsParser | None = None,
    ) -> None:
        """Initialize the middleware."""
        super().__init__(app)
        self.api_keys = api_keys or set()
        self.api_key_store = api_key_store
        self.jwt_parser = jwt_parser

    async def dispatch(
        self,
        request: Request,
        call_next: Any,
    ) -> Any:
        """Check auth on every request.

        Args:
            request (`Request`):
                The incoming request.
            call_next (`Callable`):
                The next middleware/handler.

        Returns:
            `Response`: The response, or 401 if unauthorized.
        """
        path = request.url.path

        if path in _PUBLIC_ROUTES:
            return await call_next(request)

        # Fail closed: when auth is enabled (the middleware is mounted)
        # but no API keys are configured, reject every non-public route
        # rather than letting requests through unauthenticated.
        if (
            not self.api_keys
            and self.api_key_store is None
            and self.jwt_parser is None
        ):
            return JSONResponse(
                {
                    "detail": "Authentication is enabled but no API "
                    "keys are configured (set XRUNTIME_API_KEYS or "
                    "disable server.auth_enabled).",
                },
                status_code=401,
            )

        principal = self.authenticate_headers(request.headers)
        if principal is None:
            return JSONResponse(
                {"detail": "Invalid or missing API key"},
                status_code=401,
            )

        request.state.principal = principal
        return await call_next(request)

    def authenticate_headers(self, headers: Any) -> AuthPrincipal | None:
        """Authenticate request headers and return a bound principal."""
        bearer = headers.get("authorization", "")
        if bearer.lower().startswith("bearer ") and self.jwt_parser:
            try:
                return self.jwt_parser.parse(bearer.split(" ", 1)[1])
            except (ValueError, KeyError):
                return None

        api_key = headers.get("x-api-key", "")
        if self.api_key_store is not None:
            return self.api_key_store.authenticate(api_key)
        if api_key in self.api_keys:
            from .._runtime._tenant import TenantRole

            return AuthPrincipal(
                tenant_id="default",
                user_id="anonymous",
                role=TenantRole.VIEWER,
                api_key_id=api_key,
            )
        return None
