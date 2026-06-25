# -*- coding: utf-8 -*-
"""Gateway authentication middleware — API Key / JWT."""
from __future__ import annotations

from typing import Any

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
    ) -> None:
        """Initialize the middleware."""
        super().__init__(app)
        self.api_keys = api_keys or set()

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
        if not self.api_keys:
            return JSONResponse(
                {
                    "detail": "Authentication is enabled but no API "
                    "keys are configured (set XRUNTIME_API_KEYS or "
                    "disable server.auth_enabled).",
                },
                status_code=401,
            )

        api_key = request.headers.get("x-api-key", "")

        if api_key not in self.api_keys:
            return JSONResponse(
                {"detail": "Invalid or missing API key"},
                status_code=401,
            )

        return await call_next(request)
