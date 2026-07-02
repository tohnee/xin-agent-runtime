# -*- coding: utf-8 -*-
"""Token-bucket rate limiter for the workflow runtime.

A :class:`TokenBucketRateLimiter` throttles concurrent operations
using the classic token-bucket algorithm:

* The bucket holds at most ``burst`` tokens.
* Tokens are consumed by :meth:`acquire` (one per call).
* Tokens replenish at a constant ``rate`` (tokens / second) based on
  elapsed wall-clock time (``time.monotonic``).
* When the bucket is empty, :meth:`acquire` blocks until a token is
  available or the ``timeout`` elapses.

All mutable state is guarded by an :class:`asyncio.Lock` so that
concurrent coroutines cannot over-consume the bucket.
"""
from __future__ import annotations

import asyncio
import time


class TokenBucketRateLimiter:
    """Async token-bucket rate limiter.

    Args:
        rate (`float`):
            Refill rate in tokens per second.  Must be > 0.
        burst (`int`):
            Maximum bucket capacity (initial burst).  Must be > 0.
    """

    def __init__(
        self,
        rate: float = 100.0,
        burst: int = 20,
    ) -> None:
        """Initialize the limiter."""
        if rate <= 0:
            raise ValueError(
                f"rate must be > 0, got {rate}",
            )
        if burst <= 0:
            raise ValueError(
                f"burst must be > 0, got {burst}",
            )
        self._rate = float(rate)
        self._burst = float(burst)
        # bucket starts full
        self._tokens = self._burst
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def rate(self) -> float:
        """Return the refill rate (tokens / second)."""
        return self._rate

    @property
    def burst(self) -> int:
        """Return the bucket capacity."""
        return int(self._burst)

    @property
    def available_tokens(self) -> float:
        """Return the current token count (after lazy refill)."""
        self._refill()
        return self._tokens

    def _refill(self) -> None:
        """Replenish tokens based on elapsed wall-clock time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        # elapsed >= 0 always (monotonic is non-decreasing); a
        # zero-elapsed is a harmless no-op (adds 0 tokens).
        self._tokens = min(
            self._burst,
            self._tokens + elapsed * self._rate,
        )

    async def acquire(self, timeout: float = 30.0) -> bool:
        """Acquire one token, blocking until available or timeout.

        Args:
            timeout (`float`):
                Maximum seconds to wait for a token.  ``0`` returns
                immediately if no token is available.

        Returns:
            `bool`: ``True`` if a token was acquired, ``False`` if
            the timeout elapsed first.
        """
        deadline = time.monotonic() + timeout
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                # tokens needed for the next slot
                needed = 1.0 - self._tokens
                wait = needed / self._rate
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            # sleep just long enough for one token, then retry
            await asyncio.sleep(min(wait, remaining))


__all__ = ["TokenBucketRateLimiter"]
