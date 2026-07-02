# -*- coding: utf-8 -*-
"""ShortLivedCredential — a TTL'd wrapper around a provider config.

This is **not** a :class:`agentscope.credential.CredentialBase`
subclass. It is a brokered wrapper that carries:

* ``credential_id`` — a safe-to-log token (no secret content) that
  crosses the sandbox boundary.
* ``api_key`` — a :class:`pydantic.SecretStr` that stays on the host
  and is only materialized into a real ``CredentialBase`` at the
  moment of use.
* ``issued_at`` / ``expires_at`` — TTL window (Unix epoch seconds).
* ``scopes`` — capability tokens (e.g. ``["chat", "embed"]``).
* ``audience`` — the intended sandbox id (fail-closed match).
* ``request_id`` — the gateway request that triggered the issuance.

The ``is_expired()`` / ``has_scope()`` / ``matches_audience()``
methods drive broker-side validation; ``to_provider_config()``
round-trips back to a :class:`ModelProviderConfig` so the existing
:class:`ModelResolver` can build a real credential; and
``to_injection_dict()`` produces a dict safe for writing to a
container-internal file (no ``api_key`` field).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, SecretStr

if TYPE_CHECKING:
    from .._model_resolver import ModelProviderConfig


class ShortLivedCredential(BaseModel):
    """A short-lived, scope/audience-bound credential wrapper.

    Args:
        credential_id (`str`):
            A safe-to-log token identifying this credential. Must
            not contain the secret value.
        provider_name (`str`):
            The underlying provider name (e.g. ``"openai"``).
        api_key (`SecretStr`):
            The underlying API key. Stays on the host.
        model (`str`):
            The model name to use with this credential.
        issued_at (`float`):
            Unix epoch seconds when the credential was issued.
        expires_at (`float`):
            Unix epoch seconds when the credential expires.
        base_url (`str | None`):
            Optional custom base URL.
        scopes (`list[str]`):
            Capability tokens granted to this credential.
        audience (`str`):
            The intended sandbox id. Empty string means no audience
            restriction (but :meth:`matches_audience` returns
            ``False`` for any non-empty expected audience — fail
            closed).
        request_id (`str`):
            The gateway request id that triggered issuance.
    """

    credential_id: str
    provider_name: str
    api_key: SecretStr
    model: str
    issued_at: float
    expires_at: float
    base_url: str | None = None
    scopes: list[str] = Field(default_factory=list)
    audience: str = ""
    request_id: str = ""
    tenant_id: str = ""
    session_id: str = ""

    def is_expired(self, now: float | None = None) -> bool:
        """Return ``True`` if the credential is past its TTL.

        Args:
            now (`float | None`):
                The current Unix epoch seconds. ``None`` uses
                :func:`time.time`.

        Returns:
            `bool`: ``True`` if expired.
        """
        if now is None:
            now = time.time()
        return now >= self.expires_at

    def has_scope(self, scope: str) -> bool:
        """Return ``True`` if the credential grants the given scope.

        Args:
            scope (`str`): The scope to check.

        Returns:
            `bool`: ``True`` if granted.
        """
        return scope in self.scopes

    def matches_audience(self, expected: str) -> bool:
        """Return ``True`` if the credential's audience matches.

        Empty audience never matches — fail-closed.

        Args:
            expected (`str`): The expected audience.

        Returns:
            `bool`: ``True`` if matches.
        """
        if not self.audience or not expected:
            return False
        return self.audience == expected

    def to_provider_config(self) -> "ModelProviderConfig":
        """Round-trip back to a :class:`ModelProviderConfig`.

        Used by :class:`BrokeredModelResolver` to feed the short-lived
        credential's api_key / model / base_url into the existing
        :class:`ModelResolver` materialization path.

        Returns:
            `ModelProviderConfig`: The provider config.
        """
        from .._model_resolver import ModelProviderConfig

        return ModelProviderConfig(
            name=self.provider_name,
            api_key=self.api_key.get_secret_value(),
            model=self.model,
            base_url=self.base_url,
        )

    def to_injection_dict(self) -> dict[str, Any]:
        """Produce a dict safe for writing to a container-internal file.

        The ``api_key`` is **never** included — it stays on the host
        and is materialized only at the moment of use. The container
        receives only the safe metadata (credential_id, provider,
        model, scopes, audience, expiry) so it can quote the
        credential_id back to the host when calling back for actual
        API operations.

        Returns:
            `dict[str, Any]`: Safe-to-ship metadata.
        """
        return {
            "credential_id": self.credential_id,
            "provider_name": self.provider_name,
            "model": self.model,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "scopes": list(self.scopes),
            "audience": self.audience,
            "request_id": self.request_id,
            "base_url": self.base_url,
        }
