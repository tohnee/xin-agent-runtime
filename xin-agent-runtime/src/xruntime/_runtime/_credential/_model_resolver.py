# -*- coding: utf-8 -*-
"""BrokeredModelResolver — ModelResolver + CredentialBroker integration.

Extends :class:`xruntime._runtime._model_resolver.ModelResolver` with
a :meth:`resolve_with_broker` method that returns a real
``ModelResolution`` backed by a short-lived credential issued by the
broker. The credential's api_key is materialized into a real
:class:`CredentialBase` only at the moment of use; the brokered
``credential_id`` is the only thing that crosses the sandbox
boundary.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._model_resolver import (
    ModelProviderConfig,
    ModelResolution,
    ModelResolver,
)
from ._broker import CredentialBroker

if TYPE_CHECKING:
    from agentscope.workspace import WorkspaceBase


class BrokeredModelResolver(ModelResolver):
    """A :class:`ModelResolver` that issues short-lived credentials.

    Args:
        broker (`CredentialBroker`):
            The credential broker to use for issuance.
        registry (`dict[str, ModelProviderConfig] | None`):
            Pre-registered providers, same as :class:`ModelResolver`.
    """

    def __init__(
        self,
        broker: CredentialBroker,
        registry: dict[str, ModelProviderConfig] | None = None,
    ) -> None:
        """Initialize the resolver."""
        super().__init__(registry=registry)
        self._broker = broker

    @property
    def broker(self) -> CredentialBroker:
        """Return the broker."""
        return self._broker

    def resolve_with_broker(
        self,
        *,
        provider: ModelProviderConfig,
        tenant_id: str,
        session_id: str,
        request_id: str,
        ttl_seconds: int | None = None,
        scopes: list[str] | None = None,
        audience: str = "",
    ) -> ModelResolution:
        """Resolve a provider to a ModelResolution via the broker.

        Issues (or reuses) a short-lived credential for the
        ``(tenant, session, request)`` tuple, then materializes a
        real ``CredentialBase`` from it. The credential's api_key
        never crosses the sandbox boundary — only the
        ``credential_id`` does.

        Args:
            provider (`ModelProviderConfig`):
                The underlying provider config.
            tenant_id (`str`): The tenant id.
            session_id (`str`): The session id.
            request_id (`str`): The gateway request id.
            ttl_seconds (`int | None`): Optional TTL override.
            scopes (`list[str] | None`): Optional scopes override.
            audience (`str`): Optional audience.

        Returns:
            `ModelResolution`: The resolution with a materialized
            credential.

        Raises:
            `ValueError`: If the provider name is unsupported.
        """
        short_lived = self._broker.issue_for_session(
            provider=provider,
            tenant_id=tenant_id,
            session_id=session_id,
            request_id=request_id,
            ttl_seconds=ttl_seconds,
            scopes=scopes,
            audience=audience,
        )
        # Materialize a real CredentialBase from the short-lived one.
        # Reuse the parent's _build_resolution path so all provider
        # classes (Ollama host, XAI api_host, etc.) are handled
        # consistently.
        return self._build_resolution(short_lived.to_provider_config())

    def resolve_for_workspace(
        self,
        *,
        provider: ModelProviderConfig,
        workspace: "WorkspaceBase",
        tenant_id: str,
        session_id: str,
        request_id: str,
        ttl_seconds: int | None = None,
        scopes: list[str] | None = None,
        audience: str | None = None,
    ) -> ModelResolution:
        """Resolve a provider to a ModelResolution bound to a workspace.

        Like :meth:`resolve_with_broker` but binds the short-lived
        credential's audience to ``workspace.workspace_id`` so the
        credential cannot be replayed against a different sandbox.

        Args:
            provider (`ModelProviderConfig`):
                The underlying provider config.
            workspace (`WorkspaceBase`):
                The workspace to bind the credential to.
            tenant_id (`str`): The tenant id.
            session_id (`str`): The session id.
            request_id (`str`): The gateway request id.
            ttl_seconds (`int | None`): Optional TTL override.
            scopes (`list[str] | None`): Optional scopes override.
            audience (`str | None`):
                Optional explicit audience override.  When ``None``
                (default), ``workspace.workspace_id`` is used.

        Returns:
            `ModelResolution`: The resolution with a materialized
            credential.

        Raises:
            `ValueError`: If the provider name is unsupported, or if
                any scope is not in the broker's allowlist.
        """
        short_lived = self._broker.issue_for_workspace(
            provider=provider,
            workspace=workspace,
            tenant_id=tenant_id,
            session_id=session_id,
            request_id=request_id,
            ttl_seconds=ttl_seconds,
            scopes=scopes,
            audience=audience,
        )
        return self._build_resolution(short_lived.to_provider_config())
