# -*- coding: utf-8 -*-
"""AutoRotation — proactive credential rotation before expiry.

The :class:`AutoRotationPolicy` decides *when* a credential is near
enough to expiry that it should be rotated.  The
:class:`AutoRotationManager` runs a background sweep loop that
periodically scans the broker's cache, finds rotation candidates,
issues a fresh credential through the same broker (single
chokepoint), revokes the old one only after successful issuance,
and fires ``on_rotate`` callbacks so dependents can refresh their
references.

Design notes:

* The manager never issues credentials directly — it delegates to
  :meth:`CredentialBroker.issue` so all issuance still flows through
  the broker (single chokepoint for audit / quota / scope
  enforcement).
* Rotation order is **issue-first, revoke-second** (fail-closed):
  if issuance fails, the old credential is retained so the tenant
  is never left with zero valid credentials.
* The background task is an ``asyncio.Task`` that can be started and
  stopped idempotently.  ``stop()`` cancels the task and awaits its
  completion.
* Callback exceptions are swallowed (logged) so one bad callback
  cannot break the sweep loop or starve other callbacks.

Typical usage::

    from xruntime._runtime._credential._auto_rotation import (
        AutoRotationManager,
        AutoRotationPolicy,
    )

    manager = AutoRotationManager(
        broker=broker,
        policy=AutoRotationPolicy(threshold_seconds=300),
        check_interval_seconds=60,
    )
    manager.on_rotate(lambda old, new: refresh_refs(new))
    await manager.start()
    # ... on shutdown ...
    await manager.stop()
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ._broker import CredentialBroker
    from ._short_lived import ShortLivedCredential


logger = logging.getLogger("xruntime.credential.auto_rotation")


# Type alias for rotation callbacks: (old_id, new_credential) -> None
RotationCallback = Callable[[str, "ShortLivedCredential"], None]


class AutoRotationPolicy:
    """Decide when a credential should be rotated.

    A credential is rotated when its remaining TTL falls *strictly
    below* ``threshold_seconds``.  At the exact boundary the
    credential is left alone (strict less-than avoids flapping at
    the threshold edge).

    Args:
        threshold_seconds (`int`):
            Remaining TTL below which rotation is triggered.  ``0``
            means rotate only when already expired.
    """

    def __init__(self, threshold_seconds: int = 300) -> None:
        """Initialize the policy."""
        if threshold_seconds < 0:
            raise ValueError(
                f"threshold_seconds must be >= 0, got {threshold_seconds}",
            )
        self._threshold = threshold_seconds

    @property
    def threshold_seconds(self) -> int:
        """Return the rotation threshold in seconds."""
        return self._threshold

    def should_rotate(
        self,
        cred: "ShortLivedCredential",
        now: float | None = None,
    ) -> bool:
        """Return ``True`` if the credential should be rotated.

        Rotation is triggered when ``expires_at - now`` is strictly
        less than ``threshold_seconds``.  At the exact boundary the
        credential is left alone.

        Args:
            cred (`ShortLivedCredential`): The credential to check.
            now (`float | None`):
                Current Unix epoch seconds.  ``None`` uses
                :func:`time.time`.

        Returns:
            `bool`: ``True`` if rotation should be triggered.
        """
        if now is None:
            now = time.time()
        remaining = cred.expires_at - now
        return remaining < self._threshold

    def next_rotation_at(
        self,
        cred: "ShortLivedCredential",
        now: float | None = None,
    ) -> float:
        """Return the Unix time at which this credential enters the
        rotation window.

        This is ``expires_at - threshold_seconds``.  Callers can use
        this to schedule the next sweep just-in-time rather than
        polling at a fixed interval.

        Args:
            cred (`ShortLivedCredential`): The credential.
            now (`float | None`):
                Current time (unused, kept for API symmetry).

        Returns:
            `float`: Unix epoch seconds when rotation becomes due.
        """
        return cred.expires_at - self._threshold


class AutoRotationManager:
    """Background sweeper that rotates near-expiry credentials.

    The manager scans the broker's cache, finds credentials that the
    policy says should be rotated, revokes each one, issues a fresh
    credential through the broker, and fires ``on_rotate`` callbacks
    with ``(old_id, new_credential)``.

    Args:
        broker (`CredentialBroker`):
            The broker whose cache to sweep.  All re-issuance goes
            through the broker's ``issue()`` so audit / quota / scope
            enforcement still applies.
        policy (`AutoRotationPolicy`):
            The rotation policy.
        check_interval_seconds (`float`):
            Background sweep interval in seconds.
    """

    def __init__(
        self,
        *,
        broker: "CredentialBroker",
        policy: AutoRotationPolicy,
        check_interval_seconds: float = 60.0,
    ) -> None:
        """Initialize the manager (does not start the background task)."""
        self._broker = broker
        self._policy = policy
        self._interval = check_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._callbacks: list[RotationCallback] = []

    # ── public API ───────────────────────────────────────────────

    def on_rotate(self, callback: RotationCallback) -> None:
        """Register a callback fired when a credential is rotated.

        Callbacks receive ``(old_credential_id, new_credential)``.
        Exceptions raised by callbacks are logged and swallowed so
        one bad callback cannot break the sweep.

        Args:
            callback (`RotationCallback`): The callback.
        """
        self._callbacks.append(callback)

    async def start(self) -> None:
        """Start the background sweep task (idempotent).

        Calling ``start()`` when the task is already running is a
        no-op.
        """
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background sweep task (idempotent).

        Cancels the task and awaits its completion.  Safe to call
        when the task was never started.
        """
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def sweep_once(self) -> int:
        """Run a single sweep pass.

        Scans the broker cache for rotation candidates, revokes each
        one, issues a fresh credential, and fires ``on_rotate``
        callbacks.  Exceptions in callbacks are swallowed.

        Returns:
            `int`: The number of credentials rotated.
        """
        now = time.time()
        # Snapshot the cache to avoid mutation-during-iteration
        # surprises.  We read the broker's internal cache directly
        # because the broker is the source of truth for live creds.
        candidates: list[tuple[str, ShortLivedCredential]] = []
        for cred_id, cred in list(self._broker._cache.items()):
            if cred_id in self._broker._revoked:
                continue
            if self._policy.should_rotate(cred, now=now):
                candidates.append((cred_id, cred))

        rotated = 0
        for old_id, old_cred in candidates:
            new_cred = await self._rotate_one(old_id, old_cred)
            if new_cred is not None:
                rotated += 1
                self._fire_callbacks(old_id, new_cred)
        return rotated

    # ── internals ────────────────────────────────────────────────

    async def _rotate_one(
        self,
        old_id: str,
        old_cred: "ShortLivedCredential",
    ) -> "ShortLivedCredential | None":
        """Issue a fresh credential and revoke the old one.

        Re-issuance reuses the old credential's provider / scopes /
        audience / TTL window so the new credential is a drop-in
        replacement.  The new credential is issued *first*; only
        after successful issuance is the old one revoked
        (fail-closed: if issuance fails, the old credential is
        retained so the tenant is never left without a valid
        credential).

        Args:
            old_id (`str`): The old credential id.
            old_cred (`ShortLivedCredential`): The old credential.

        Returns:
            `ShortLivedCredential | None`:
                The new credential, or ``None`` if re-issuance
                failed (old credential retained in that case).
        """
        # Build a provider config from the old credential so we can
        # re-issue through the broker's normal path.
        from .._model_resolver import ModelProviderConfig

        provider = ModelProviderConfig(
            name=old_cred.provider_name,
            api_key=old_cred.api_key.get_secret_value(),
            model=old_cred.model,
            base_url=old_cred.base_url,
        )
        # Issue new credential FIRST, then revoke old one.
        # This prevents tenant losing all credentials if issue fails.
        try:
            new_cred = self._broker.issue(
                provider=provider,
                tenant_id=old_cred.tenant_id,
                session_id=old_cred.session_id,
                request_id=old_cred.request_id,
                ttl_seconds=None,  # use broker default
                scopes=list(old_cred.scopes),
                audience=old_cred.audience,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to re-issue credential during rotation for "
                "old_id=%s — old credential retained",
                old_id,
            )
            return None
        # Issue succeeded — now safe to revoke the old one.
        self._broker.revoke(old_id)
        return new_cred

    def _fire_callbacks(
        self,
        old_id: str,
        new_cred: "ShortLivedCredential",
    ) -> None:
        """Fire all registered ``on_rotate`` callbacks.

        Exceptions are logged and swallowed so one bad callback
        cannot starve the others or break the sweep loop.

        Args:
            old_id (`str`): The revoked credential id.
            new_cred (`ShortLivedCredential`): The fresh credential.
        """
        for cb in self._callbacks:
            try:
                cb(old_id, new_cred)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "on_rotate callback raised for old_id=%s",
                    old_id,
                )

    async def _run_loop(self) -> None:
        """The background sweep loop.

        Runs forever until cancelled.  Each iteration sleeps for
        ``check_interval_seconds`` then calls :meth:`sweep_once`.
        Exceptions in :meth:`sweep_once` are logged so the loop
        never dies from a transient error.
        """
        try:
            while True:
                await asyncio.sleep(self._interval)
                try:
                    await self.sweep_once()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "AutoRotation sweep_once raised; loop " "continuing",
                    )
        except asyncio.CancelledError:
            # Normal shutdown path
            raise


# ── helpers ──────────────────────────────────────────────────────
