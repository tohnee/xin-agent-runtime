# -*- coding: utf-8 -*-
"""Rate limiter — sliding window per-client rate limiting."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Deque


class RateLimiter:
    """Sliding-window rate limiter.

    Tracks request timestamps per client identifier.  Requests
    within the window count toward the limit; the window slides
    forward as old requests expire.

    Args:
        max_requests (`int`):
            Maximum requests allowed within the window.
        window_seconds (`float`):
            Sliding window duration in seconds.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
    ) -> None:
        """Initialize the rate limiter."""
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    async def check(self, client_id: str) -> bool:
        """Check if a client is within the rate limit.

        Args:
            client_id (`str`):
                Client identifier (e.g. API key, IP, tenant).

        Returns:
            `bool`: ``True`` if the request is allowed, ``False``
            if rate-limited.
        """
        now = time.monotonic()
        window_start = now - self.window_seconds

        hits = self._hits.get(client_id)

        # Evict expired entries
        if hits is not None:
            while hits and hits[0] < window_start:
                hits.popleft()
            # Drop clients whose window is fully expired so the map
            # does not grow unbounded across many distinct clients.
            if not hits:
                del self._hits[client_id]
                hits = None

        if hits is not None and len(hits) >= self.max_requests:
            return False

        if hits is None:
            hits = self._hits[client_id]
        hits.append(now)
        return True


# Routes that bypass rate limiting (health / docs probes).
_RATELIMIT_EXEMPT: set[str] = {
    "/health",
    "/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
}


class RateLimitMiddleware:
    """ASGI middleware that enforces a :class:`RateLimiter`.

    Clients are keyed by ``x-api-key`` header when present, otherwise
    by tenant id (``x-tenant-id``) falling back to the peer IP. Requests
    over the limit get a ``429`` response. Health / docs routes are
    exempt so probes are never throttled.

    Args:
        app (`Any`):
            The ASGI app to wrap.
        limiter (`RateLimiter`):
            The sliding-window limiter to enforce.
    """

    def __init__(self, app: Any, limiter: "RateLimiter") -> None:
        """Initialize the middleware."""
        self.app = app
        self.limiter = limiter

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        """Enforce the rate limit on HTTP requests.

        Args:
            scope (`dict`): The ASGI connection scope.
            receive (`Callable`): The ASGI receive channel.
            send (`Callable`): The ASGI send channel.
        """
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _RATELIMIT_EXEMPT:
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        client_id = (
            headers.get("x-api-key")
            or headers.get("x-tenant-id")
            or (scope.get("client") or ["anonymous"])[0]
        )

        allowed = await self.limiter.check(client_id)
        if not allowed:
            await self._send_429(send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_429(send: Any) -> None:
        """Send a 429 Too Many Requests JSON response.

        Args:
            send (`Callable`): The ASGI send channel.
        """
        import json

        body = json.dumps(
            {"detail": "Rate limit exceeded"},
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", b"1"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )
