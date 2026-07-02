# -*- coding: utf-8 -*-
"""CredentialBroker — issue / validate / revoke short-lived credentials.

The broker is the single chokepoint where long-lived provider
secrets are exchanged for short-lived, scope/audience-bound
``credential_id`` tokens. Secrets never leave the broker; only the
``credential_id`` (a safe-to-log opaque string) crosses the sandbox
boundary.

Caching strategy:

* Per ``(tenant_id, session_id, request_id)`` — so repeated calls
  within the same turn reuse the same token (avoids minting a new
  one for every tool call).
* TTL-based eviction — expired entries are evicted on access or via
  :meth:`evict_expired`.
* LRU on ``cache_max_size`` — when the cache exceeds the configured
  max, the oldest entries are dropped.

Invalidation hook:

* :meth:`on_revoke` registers a callback fired whenever a credential
  is revoked. The gateway uses this to drop its cached credential
  id so subsequent requests in the same session get a fresh one.
* :meth:`drain_invalidations` returns the set of revoked ids since
  the last drain, then clears the queue. Used by the gateway on
  each request to invalidate its cache.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from pydantic import SecretStr

from ._config import CredentialBrokerConfig
from ._short_lived import ShortLivedCredential

if TYPE_CHECKING:
    from agentscope.workspace import WorkspaceBase

    from .._model_resolver import ModelProviderConfig


@dataclass
class ValidationResult:
    """Result of validating a short-lived credential.

    Attributes:
        is_valid (`bool`): Whether the credential is currently valid.
        reason (`str`): Human-readable reason if invalid; empty if valid.
        credential (`ShortLivedCredential | None`): The credential
            if found, otherwise ``None``.
    """

    is_valid: bool
    reason: str = ""
    credential: ShortLivedCredential | None = None


logger = logging.getLogger("xruntime.credential.broker")


class CredentialBroker:
    """Issue, validate, and revoke short-lived credentials.

    Args:
        config (`CredentialBrokerConfig`):
            Broker config. Defaults to a fresh
            :class:`CredentialBrokerConfig`.
    """

    def __init__(
        self,
        config: CredentialBrokerConfig | None = None,
    ) -> None:
        """Initialize the broker."""
        self._config = config or CredentialBrokerConfig()
        # credential_id -> ShortLivedCredential (insertion-ordered for LRU)
        self._cache: "OrderedDict[str, ShortLivedCredential]" = OrderedDict()
        # credential_id -> revoked flag
        self._revoked: set[str] = set()
        # (tenant_id, session_id, request_id) -> credential_id
        self._session_index: dict[tuple[str, str, str], str] = {}
        # Pending invalidations (drained by the gateway on each request)
        self._pending_invalidations: set[str] = set()
        # Revocation callbacks
        self._on_revoke_callbacks: list[Callable[[str], None]] = []

    @property
    def config(self) -> CredentialBrokerConfig:
        """Return the broker config."""
        return self._config

    def issue(
        self,
        *,
        provider: "ModelProviderConfig",
        tenant_id: str,
        session_id: str,
        request_id: str,
        ttl_seconds: int | None = None,
        scopes: list[str] | None = None,
        audience: str = "",
    ) -> ShortLivedCredential:
        """Issue a new short-lived credential.

        Args:
            provider (`ModelProviderConfig`):
                The underlying provider config (carries the secret).
            tenant_id (`str`):
                The tenant requesting the credential.
            session_id (`str`):
                The session the credential is scoped to.
            request_id (`str`):
                The gateway request id.
            ttl_seconds (`int | None`):
                TTL in seconds. ``None`` uses
                ``config.default_ttl_seconds``. Clamped to
                ``config.max_ttl_seconds``.
            scopes (`list[str] | None`):
                Scopes to grant. ``None`` uses
                ``config.default_scopes``.  When
                ``config.allowed_scopes`` is non-empty, every
                effective scope must be in the allowlist.
            audience (`str`):
                The intended sandbox id.

        Returns:
            `ShortLivedCredential`: The issued credential.

        Raises:
            `ValueError`: If any effective scope is not in
                ``config.allowed_scopes`` (when the allowlist is
                non-empty).
        """
        effective_ttl = ttl_seconds or self._config.default_ttl_seconds
        effective_ttl = min(effective_ttl, self._config.max_ttl_seconds)
        effective_scopes = list(
            scopes if scopes is not None else self._config.default_scopes,
        )
        self._enforce_scope_allowlist(effective_scopes)

        now = time.time()
        cred = ShortLivedCredential(
            credential_id=f"slc-{uuid.uuid4().hex}",
            provider_name=provider.name,
            api_key=SecretStr(provider.api_key),
            model=provider.model,
            issued_at=now,
            expires_at=now + effective_ttl,
            base_url=provider.base_url,
            scopes=effective_scopes,
            audience=audience,
            request_id=request_id,
        )
        self._store(
            cred,
            tenant_id=tenant_id,
            session_id=session_id,
            request_id=request_id,
        )
        return cred

    def issue_for_session(
        self,
        *,
        provider: "ModelProviderConfig",
        tenant_id: str,
        session_id: str,
        request_id: str,
        ttl_seconds: int | None = None,
        scopes: list[str] | None = None,
        audience: str = "",
    ) -> ShortLivedCredential:
        """Issue a credential for a session, reusing an active one if present.

        If a non-expired, non-revoked credential already exists for
        the ``(tenant, session, request)`` tuple, it is returned
        instead of minting a new one. Otherwise a new credential is
        issued and cached.

        Args:
            Same as :meth:`issue`.

        Returns:
            `ShortLivedCredential`: The (possibly cached) credential.
        """
        key = (tenant_id, session_id, request_id)
        existing_id = self._session_index.get(key)
        if existing_id is not None:
            existing = self._cache.get(existing_id)
            if (
                existing is not None
                and not existing.is_expired()
                and existing_id not in self._revoked
            ):
                # Move to end (LRU freshness)
                self._cache.move_to_end(existing_id)
                return existing
        # No active cached credential — issue a new one
        return self.issue(
            provider=provider,
            tenant_id=tenant_id,
            session_id=session_id,
            request_id=request_id,
            ttl_seconds=ttl_seconds,
            scopes=scopes,
            audience=audience,
        )

    def validate(
        self,
        credential_id: str,
        *,
        expected_audience: str | None = None,
        required_scopes: list[str] | None = None,
    ) -> ValidationResult:
        """Validate a credential by id.

        Args:
            credential_id (`str`): The credential id to validate.
            expected_audience (`str | None`):
                If given, the credential's audience must match.
            required_scopes (`list[str] | None`):
                If given, the credential must grant all listed scopes.

        Returns:
            `ValidationResult`: The validation result.
        """
        cred = self._cache.get(credential_id)
        if cred is None:
            return ValidationResult(
                is_valid=False,
                reason=f"Credential {credential_id!r} not found",
                credential=None,
            )
        if credential_id in self._revoked:
            return ValidationResult(
                is_valid=False,
                reason=f"Credential {credential_id!r} is revoked",
                credential=cred,
            )
        if cred.is_expired():
            return ValidationResult(
                is_valid=False,
                reason=f"Credential {credential_id!r} is expired",
                credential=cred,
            )
        if expected_audience is not None and not cred.matches_audience(
            expected_audience,
        ):
            return ValidationResult(
                is_valid=False,
                reason=(
                    f"Credential audience mismatch: expected "
                    f"{expected_audience!r}, got {cred.audience!r}"
                ),
                credential=cred,
            )
        if required_scopes:
            missing = [s for s in required_scopes if not cred.has_scope(s)]
            if missing:
                return ValidationResult(
                    is_valid=False,
                    reason=(f"Credential missing required scopes: {missing}"),
                    credential=cred,
                )
        return ValidationResult(
            is_valid=True,
            reason="",
            credential=cred,
        )

    def revoke(self, credential_id: str) -> None:
        """Revoke a credential by id.

        Idempotent — revoking an unknown or already-revoked credential
        is a no-op. Fires any registered ``on_revoke`` callbacks and
        records the id in the pending invalidations queue.

        Args:
            credential_id (`str`): The credential id to revoke.
        """
        if credential_id not in self._cache:
            return
        if credential_id in self._revoked:
            return
        self._revoked.add(credential_id)
        self._pending_invalidations.add(credential_id)
        for cb in self._on_revoke_callbacks:
            try:
                cb(credential_id)
            except Exception:  # noqa: BLE001
                # Callback failures must not break the revoke path,
                # but we log them so operators can detect stale caches.
                logger.exception(
                    "on_revoke callback failed for %s",
                    credential_id,
                )

    def get(self, credential_id: str) -> ShortLivedCredential | None:
        """Return the credential by id, or ``None`` if not cached.

        Args:
            credential_id (`str`): The credential id.

        Returns:
            `ShortLivedCredential | None`: The credential, or ``None``.
        """
        return self._cache.get(credential_id)

    def evict_expired(self) -> int:
        """Evict all expired credentials from the cache.

        Returns:
            `int`: The number of credentials evicted.
        """
        now = time.time()
        expired_ids = [
            cid for cid, c in self._cache.items() if c.is_expired(now)
        ]
        for cid in expired_ids:
            self._remove(cid)
        return len(expired_ids)

    def on_revoke(self, callback: Callable[[str], None]) -> None:
        """Register a callback fired when a credential is revoked.

        Args:
            callback (`Callable[[str], None]`): The callback. Receives
                the revoked credential id.
        """
        self._on_revoke_callbacks.append(callback)

    def drain_invalidations(self) -> set[str]:
        """Return the set of revoked credential ids since the last drain.

        The queue is cleared after draining so the gateway can poll
        it on each request and drop its cached credential ids.

        Returns:
            `set[str]`: The revoked credential ids.
        """
        invalidations = set(self._pending_invalidations)
        self._pending_invalidations.clear()
        return invalidations

    # ── workspace-bound issuance ───────────────────────────────────

    def issue_for_workspace(
        self,
        *,
        provider: "ModelProviderConfig",
        workspace: "WorkspaceBase",
        tenant_id: str,
        session_id: str,
        request_id: str,
        ttl_seconds: int | None = None,
        scopes: list[str] | None = None,
        audience: str | None = None,
    ) -> ShortLivedCredential:
        """Issue a credential bound to a workspace's stable id.

        The workspace's ``workspace_id`` is used as the credential's
        ``audience`` so the credential cannot be replayed against a
        different sandbox.  If ``audience`` is explicitly provided,
        it overrides the workspace id (escape hatch for non-standard
        bindings).

        Args:
            provider (`ModelProviderConfig`):
                The underlying provider config.
            workspace (`WorkspaceBase`):
                The workspace to bind the credential to.  Must expose
                a ``workspace_id`` attribute.
            tenant_id (`str`): The tenant id.
            session_id (`str`): The session id.
            request_id (`str`): The gateway request id.
            ttl_seconds (`int | None`): Optional TTL override.
            scopes (`list[str] | None`): Optional scopes override.
            audience (`str | None`):
                Optional explicit audience override.  When ``None``
                (default), ``workspace.workspace_id`` is used.

        Returns:
            `ShortLivedCredential`: The issued (or reused) credential.

        Raises:
            `ValueError`: If any effective scope is not in
                ``config.allowed_scopes``.
            `AttributeError`: If ``workspace`` has no
                ``workspace_id`` attribute.
        """
        effective_audience = (
            audience if audience is not None else workspace.workspace_id
        )
        return self.issue_for_session(
            provider=provider,
            tenant_id=tenant_id,
            session_id=session_id,
            request_id=request_id,
            ttl_seconds=ttl_seconds,
            scopes=scopes,
            audience=effective_audience,
        )

    # ── bulk revoke by audience (teardown) ─────────────────────────

    def revoke_for_audience(self, audience: str) -> int:
        """Revoke all non-expired credentials with a matching audience.

        Used when a sandbox is torn down — any short-lived credentials
        issued for it must be revoked so they cannot be replayed even
        before their TTL expires.  Already-revoked credentials are
        skipped (no double-counting).

        Each newly-revoked credential is recorded in the pending
        invalidations queue (drained by the gateway on the next
        request).

        Args:
            audience (`str`):
                The audience to match.  Empty string matches
                "unbound" credentials (issued without a workspace).

        Returns:
            `int`: The number of credentials newly revoked.
        """
        newly_revoked = 0
        for cid, cred in list(self._cache.items()):
            if cid in self._revoked:
                continue
            if cred.is_expired():
                continue
            if cred.audience == audience:
                self.revoke(cid)
                newly_revoked += 1
        return newly_revoked

    def revoke_for_workspace(self, workspace: "WorkspaceBase") -> int:
        """Revoke all credentials bound to a workspace.

        Convenience wrapper around :meth:`revoke_for_audience` that
        uses ``workspace.workspace_id`` as the audience.

        Args:
            workspace (`WorkspaceBase`):
                The workspace being torn down.

        Returns:
            `int`: The number of credentials newly revoked.
        """
        return self.revoke_for_audience(workspace.workspace_id)

    # ── internal ────────────────────────────────────────────────────

    def _store(
        self,
        cred: ShortLivedCredential,
        *,
        tenant_id: str,
        session_id: str,
        request_id: str,
    ) -> None:
        """Store a credential and index it by session tuple."""
        self._cache[cred.credential_id] = cred
        self._session_index[
            (tenant_id, session_id, request_id)
        ] = cred.credential_id
        # LRU eviction if over max size
        while len(self._cache) > self._config.cache_max_size:
            oldest_id, _ = self._cache.popitem(last=False)
            self._remove_from_index(oldest_id)

    def _remove(self, credential_id: str) -> None:
        """Remove a credential from cache and indices."""
        self._cache.pop(credential_id, None)
        self._revoked.discard(credential_id)
        self._remove_from_index(credential_id)

    def _remove_from_index(self, credential_id: str) -> None:
        """Remove a credential id from the session index."""
        # Iterate values — small dict, infrequent operation
        for key, cid in list(self._session_index.items()):
            if cid == credential_id:
                del self._session_index[key]

    def _enforce_scope_allowlist(self, scopes: list[str]) -> None:
        """Reject scopes not in ``config.allowed_scopes``.

        When ``config.allowed_scopes`` is empty, no enforcement is
        applied (backward compatible).  When non-empty, every scope in
        ``scopes`` must be in the allowlist.

        Args:
            scopes (`list[str]`): The effective scopes to check.

        Raises:
            `ValueError`: If any scope is not in the allowlist.
        """
        if not self._config.allowed_scopes:
            return
        allowed = set(self._config.allowed_scopes)
        rejected = [s for s in scopes if s not in allowed]
        if rejected:
            raise ValueError(
                f"Scope(s) {rejected!r} not in allowed_scopes "
                f"({sorted(allowed)!r})",
            )
