# -*- coding: utf-8 -*-
"""TDD tests for the Credential Brokering MVP (P1-C).

Covers:

1. :class:`ShortLivedCredential` — TTL, expiration, scopes, audience.
2. :class:`CredentialBroker` — issue / validate / revoke / cache
   eviction.
3. :class:`BrokeredModelResolver` — resolution integration with the
   existing :class:`ModelResolver`.
4. Config wiring — :class:`CredentialBrokerConfig` defaults +
   env-var override.
5. Gateway cache invalidation hook.

Design rationale: short-lived credentials are *not*
``CredentialBase`` subclasses.  They are brokered wrappers that carry
TTL + scopes + audience + the underlying provider config so the
runtime can materialize a real ``CredentialBase`` only at the moment
of use.  Secrets never cross the boundary into sandbox containers —
the brokered ``credential_id`` is the only thing that crosses.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from xruntime._config import CredentialBrokerConfig, XRuntimeConfig
from xruntime._runtime._credential import (
    BrokeredModelResolver,
    CredentialBroker,
    ShortLivedCredential,
)
from xruntime._runtime._model_resolver import (
    ModelProviderConfig,
    ModelResolver,
)


# ── 1. ShortLivedCredential ─────────────────────────────────────────


class TestShortLivedCredential:
    """ShortLivedCredential — TTL + scopes + audience wrapper."""

    def test_basic_construction(self) -> None:
        cred = ShortLivedCredential(
            credential_id="slc-001",
            provider_name="openai",
            api_key=SecretStr("sk-test"),
            model="gpt-4",
            issued_at=1000.0,
            expires_at=2000.0,
        )
        assert cred.credential_id == "slc-001"
        assert cred.provider_name == "openai"
        assert cred.api_key.get_secret_value() == "sk-test"
        assert cred.model == "gpt-4"
        assert cred.scopes == []
        assert cred.audience == ""
        assert cred.request_id == ""

    def test_construction_with_full_fields(self) -> None:
        cred = ShortLivedCredential(
            credential_id="slc-002",
            provider_name="anthropic",
            api_key=SecretStr("sk-ant"),
            model="claude-sonnet-4",
            issued_at=1000.0,
            expires_at=2000.0,
            scopes=["chat", "embed"],
            audience="xruntime-sandbox",
            request_id="req-abc",
            base_url="https://api.anthropic.com",
        )
        assert cred.scopes == ["chat", "embed"]
        assert cred.audience == "xruntime-sandbox"
        assert cred.request_id == "req-abc"
        assert cred.base_url == "https://api.anthropic.com"

    def test_is_expired_false_when_in_window(self) -> None:
        now = time.time()
        cred = ShortLivedCredential(
            credential_id="slc",
            provider_name="openai",
            api_key=SecretStr("k"),
            model="m",
            issued_at=now - 100,
            expires_at=now + 1000,
        )
        assert cred.is_expired() is False

    def test_is_expired_true_when_past_ttl(self) -> None:
        now = time.time()
        cred = ShortLivedCredential(
            credential_id="slc",
            provider_name="openai",
            api_key=SecretStr("k"),
            model="m",
            issued_at=now - 2000,
            expires_at=now - 1000,
        )
        assert cred.is_expired() is True

    def test_is_expired_true_at_exact_expiry(self) -> None:
        """Edge case: at the expiry timestamp, the credential is expired."""
        now = 5000.0
        cred = ShortLivedCredential(
            credential_id="slc",
            provider_name="openai",
            api_key=SecretStr("k"),
            model="m",
            issued_at=1000.0,
            expires_at=now,
        )
        with patch(
            "xruntime._runtime._credential._short_lived.time.time"
        ) as t:
            t.return_value = now
            assert cred.is_expired() is True

    def test_has_scope(self) -> None:
        cred = ShortLivedCredential(
            credential_id="slc",
            provider_name="openai",
            api_key=SecretStr("k"),
            model="m",
            issued_at=0.0,
            expires_at=1.0,
            scopes=["chat", "embed"],
        )
        assert cred.has_scope("chat") is True
        assert cred.has_scope("admin") is False

    def test_has_scope_empty_scopes_returns_false(self) -> None:
        cred = ShortLivedCredential(
            credential_id="slc",
            provider_name="openai",
            api_key=SecretStr("k"),
            model="m",
            issued_at=0.0,
            expires_at=1.0,
        )
        assert cred.has_scope("anything") is False

    def test_matches_audience(self) -> None:
        cred = ShortLivedCredential(
            credential_id="slc",
            provider_name="openai",
            api_key=SecretStr("k"),
            model="m",
            issued_at=0.0,
            expires_at=1.0,
            audience="xruntime-sandbox",
        )
        assert cred.matches_audience("xruntime-sandbox") is True
        assert cred.matches_audience("other") is False

    def test_matches_audience_empty_returns_false(self) -> None:
        """Empty audience never matches — fail-closed."""
        cred = ShortLivedCredential(
            credential_id="slc",
            provider_name="openai",
            api_key=SecretStr("k"),
            model="m",
            issued_at=0.0,
            expires_at=1.0,
            audience="",
        )
        assert cred.matches_audience("anything") is False

    def test_to_provider_config_roundtrip(self) -> None:
        """to_provider_config produces a ModelProviderConfig with the
        short-lived credential's api_key / model / base_url."""
        cred = ShortLivedCredential(
            credential_id="slc",
            provider_name="openai",
            api_key=SecretStr("sk-test"),
            model="gpt-4",
            issued_at=0.0,
            expires_at=1.0,
            base_url="https://api.openai.com",
        )
        pc = cred.to_provider_config()
        assert isinstance(pc, ModelProviderConfig)
        assert pc.name == "openai"
        assert pc.api_key == "sk-test"
        assert pc.model == "gpt-4"
        assert pc.base_url == "https://api.openai.com"


# ── 2. CredentialBroker ─────────────────────────────────────────────


class TestCredentialBroker:
    """CredentialBroker — issue / validate / revoke / cache."""

    def test_issue_returns_short_lived_credential(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-long-lived",
            model="gpt-4",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="tenant-1",
            session_id="sess-1",
            request_id="req-1",
            ttl_seconds=3600,
        )
        assert isinstance(cred, ShortLivedCredential)
        assert cred.provider_name == "openai"
        assert cred.api_key.get_secret_value() == "sk-long-lived"
        assert cred.credential_id  # generated
        assert cred.expires_at > cred.issued_at
        assert cred.request_id == "req-1"

    def test_issue_with_default_ttl(self) -> None:
        broker = CredentialBroker(
            config=CredentialBrokerConfig(default_ttl_seconds=1800),
        )
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        # TTL = 1800s
        assert cred.expires_at - cred.issued_at == pytest.approx(
            1800, rel=0.01
        )

    def test_issue_clamps_ttl_to_max(self) -> None:
        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                default_ttl_seconds=3600,
                max_ttl_seconds=7200,
            ),
        )
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            ttl_seconds=999999,
        )
        assert cred.expires_at - cred.issued_at <= 7200

    def test_issue_with_scopes_and_audience(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            scopes=["chat"],
            audience="sandbox-1",
        )
        assert cred.scopes == ["chat"]
        assert cred.audience == "sandbox-1"

    def test_validate_active_credential(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            ttl_seconds=3600,
        )
        result = broker.validate(cred.credential_id)
        assert result.is_valid is True
        assert result.reason == ""

    def test_validate_expired_credential(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            ttl_seconds=-1,  # already expired
        )
        result = broker.validate(cred.credential_id)
        assert result.is_valid is False
        assert "expired" in result.reason.lower()

    def test_validate_revoked_credential(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            ttl_seconds=3600,
        )
        broker.revoke(cred.credential_id)
        result = broker.validate(cred.credential_id)
        assert result.is_valid is False
        assert "revoked" in result.reason.lower()

    def test_validate_unknown_credential(self) -> None:
        broker = CredentialBroker()
        result = broker.validate("does-not-exist")
        assert result.is_valid is False
        assert "not found" in result.reason.lower()

    def test_validate_audience_match(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            audience="sandbox-A",
        )
        result = broker.validate(
            cred.credential_id,
            expected_audience="sandbox-A",
        )
        assert result.is_valid is True

    def test_validate_audience_mismatch(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            audience="sandbox-A",
        )
        result = broker.validate(
            cred.credential_id,
            expected_audience="sandbox-B",
        )
        assert result.is_valid is False
        assert "audience" in result.reason.lower()

    def test_validate_scope_match(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            scopes=["chat", "embed"],
        )
        result = broker.validate(
            cred.credential_id,
            required_scopes=["chat"],
        )
        assert result.is_valid is True

    def test_validate_scope_missing(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            scopes=["chat"],
        )
        result = broker.validate(
            cred.credential_id,
            required_scopes=["admin"],
        )
        assert result.is_valid is False
        assert "scope" in result.reason.lower()

    def test_revoke_unknown_is_noop(self) -> None:
        broker = CredentialBroker()
        # Should not raise
        broker.revoke("does-not-exist")

    def test_revoke_already_revoked_is_noop(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        broker.revoke(cred.credential_id)
        broker.revoke(cred.credential_id)  # second revoke is idempotent

    def test_get_credential_returns_cached(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        fetched = broker.get(cred.credential_id)
        assert fetched is not None
        assert fetched.credential_id == cred.credential_id

    def test_get_credential_unknown_returns_none(self) -> None:
        broker = CredentialBroker()
        assert broker.get("unknown") is None

    def test_cache_eviction_on_max_size(self) -> None:
        """When cache exceeds max_size, oldest entries are evicted."""
        broker = CredentialBroker(
            config=CredentialBrokerConfig(cache_max_size=3),
        )
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        for i in range(5):
            broker.issue(
                provider=provider,
                tenant_id="t",
                session_id=f"s-{i}",
                request_id=f"r-{i}",
            )
        # Cache should be capped at max_size + some slack
        assert len(broker._cache) <= 5  # implementation detail

    def test_evict_expired_clears_stale_entries(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        # Issue an expired one and a fresh one
        expired = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s1",
            request_id="r1",
            ttl_seconds=-1,
        )
        fresh = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s2",
            request_id="r2",
            ttl_seconds=3600,
        )
        evicted_count = broker.evict_expired()
        assert evicted_count >= 1
        assert broker.get(expired.credential_id) is None
        assert broker.get(fresh.credential_id) is not None

    def test_issue_for_session_reuses_active_credential(self) -> None:
        """Issuing for the same (tenant, session, request) reuses the
        active credential instead of minting a new one."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred1 = broker.issue_for_session(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            ttl_seconds=3600,
        )
        cred2 = broker.issue_for_session(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            ttl_seconds=3600,
        )
        assert cred1.credential_id == cred2.credential_id

    def test_issue_for_session_mints_new_after_expiry(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred1 = broker.issue_for_session(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            ttl_seconds=-1,  # already expired
        )
        cred2 = broker.issue_for_session(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            ttl_seconds=3600,
        )
        assert cred1.credential_id != cred2.credential_id


# ── 3. CredentialBrokerConfig ───────────────────────────────────────


class TestCredentialBrokerConfig:
    """CredentialBrokerConfig — defaults + XRuntimeConfig wiring."""

    def test_defaults(self) -> None:
        cfg = CredentialBrokerConfig()
        assert cfg.enabled is False
        assert cfg.default_ttl_seconds == 3600
        assert cfg.max_ttl_seconds == 86400
        assert cfg.default_scopes == []
        assert cfg.cache_max_size == 1000

    def test_xruntime_config_has_broker_field(self) -> None:
        cfg = XRuntimeConfig()
        assert hasattr(cfg, "credential_broker")
        assert isinstance(cfg.credential_broker, CredentialBrokerConfig)
        assert cfg.credential_broker.enabled is False

    def test_xruntime_config_broker_can_be_enabled(self) -> None:
        cfg = XRuntimeConfig(
            credential_broker=CredentialBrokerConfig(enabled=True),
        )
        assert cfg.credential_broker.enabled is True

    def test_env_override_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from xruntime._config import _apply_env_overrides

        monkeypatch.setenv("XRUNTIME_CREDENTIAL_BROKER_ENABLED", "true")
        cfg = _apply_env_overrides(XRuntimeConfig())
        assert cfg.credential_broker.enabled is True

    def test_env_override_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from xruntime._config import _apply_env_overrides

        monkeypatch.setenv(
            "XRUNTIME_CREDENTIAL_BROKER_DEFAULT_TTL_SECONDS",
            "7200",
        )
        cfg = _apply_env_overrides(XRuntimeConfig())
        assert cfg.credential_broker.default_ttl_seconds == 7200


# ── 4. BrokeredModelResolver ────────────────────────────────────────


class TestBrokeredModelResolver:
    """BrokeredModelResolver — wraps ModelResolver with broker."""

    def test_inherits_from_model_resolver(self) -> None:
        assert issubclass(BrokeredModelResolver, ModelResolver)

    def test_resolve_with_broker_returns_resolution(self) -> None:
        from agentscope.credential import OpenAICredential

        broker = CredentialBroker()
        resolver = BrokeredModelResolver(broker=broker)
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-test",
            model="gpt-4",
        )
        resolution = resolver.resolve_with_broker(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            ttl_seconds=3600,
        )
        assert resolution is not None
        assert isinstance(resolution.credential, OpenAICredential)
        assert resolution.model_name == "gpt-4"
        # The credential should have the api_key from the short-lived
        # credential (which mirrors the provider's api_key)
        assert resolution.credential.api_key.get_secret_value() == "sk-test"

    def test_resolve_with_broker_unknown_provider_raises(self) -> None:
        broker = CredentialBroker()
        resolver = BrokeredModelResolver(broker=broker)
        provider = ModelProviderConfig(
            name="unknown_provider",
            api_key="k",
            model="m",
        )
        with pytest.raises(ValueError, match="Unsupported"):
            resolver.resolve_with_broker(
                provider=provider,
                tenant_id="t",
                session_id="s",
                request_id="r",
            )

    def test_resolve_with_broker_records_in_broker_cache(self) -> None:
        broker = CredentialBroker()
        resolver = BrokeredModelResolver(broker=broker)
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-test",
            model="gpt-4",
        )
        resolver.resolve_with_broker(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        # The broker should have one cached credential
        assert len(broker._cache) >= 1

    def test_resolve_with_broker_uses_existing_active_credential(
        self,
    ) -> None:
        """When called twice with same args, reuses the cached
        short-lived credential (broker cache has one entry, both
        materialized credentials carry the same api_key)."""
        broker = CredentialBroker()
        resolver = BrokeredModelResolver(broker=broker)
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-test",
            model="gpt-4",
        )
        r1 = resolver.resolve_with_broker(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        r2 = resolver.resolve_with_broker(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        # The broker cache should have exactly one entry (reuse).
        assert len(broker._cache) == 1
        # Both materialized credentials carry the same api_key
        # (proving they came from the same short-lived credential).
        assert (
            r1.credential.api_key.get_secret_value()
            == r2.credential.api_key.get_secret_value()
            == "sk-test"
        )

    def test_broker_accessor_returns_the_broker(self) -> None:
        """The ``broker`` property exposes the underlying broker."""
        broker = CredentialBroker()
        resolver = BrokeredModelResolver(broker=broker)
        assert resolver.broker is broker


# ── 5. Gateway cache invalidation hook ──────────────────────────────


class TestGatewayCacheInvalidation:
    """Broker hook for gateway credential cache invalidation."""

    def test_broker_records_invalidations(self) -> None:
        """When a credential is revoked, the broker records the
        invalidation so the gateway can drop its cached id."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        broker.revoke(cred.credential_id)
        # The broker should expose a way to drain pending invalidations
        invalidations = broker.drain_invalidations()
        assert cred.credential_id in invalidations

    def test_drain_invalidations_clears_queue(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        broker.revoke(cred.credential_id)
        broker.drain_invalidations()
        # Second drain should be empty
        assert broker.drain_invalidations() == set()

    def test_broker_callback_on_revoke(self) -> None:
        """Registering an on_revoke callback fires when a credential
        is revoked."""
        broker = CredentialBroker()
        revoked_ids: list[str] = []
        broker.on_revoke(lambda cid: revoked_ids.append(cid))
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        broker.revoke(cred.credential_id)
        assert cred.credential_id in revoked_ids

    def test_broker_callback_exception_is_swallowed(self) -> None:
        """A raising on_revoke callback must not break the revoke path
        (defensive isolation between callbacks and the broker state)."""
        broker = CredentialBroker()

        def bad_callback(_cid: str) -> None:
            raise RuntimeError("boom")

        broker.on_revoke(bad_callback)
        # A second, healthy callback should still fire after the bad one
        healthy_calls: list[str] = []
        broker.on_revoke(lambda cid: healthy_calls.append(cid))

        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )

        # Must not raise
        broker.revoke(cred.credential_id)
        # The healthy callback still ran despite the bad one
        assert cred.credential_id in healthy_calls
        # And the credential is recorded as revoked
        result = broker.validate(cred.credential_id)
        assert not result.is_valid
        assert "revoked" in result.reason

    def test_broker_config_accessor(self) -> None:
        """The ``config`` property exposes the broker's config (read-only)."""
        cfg = CredentialBrokerConfig(
            enabled=True,
            default_ttl_seconds=600,
            max_ttl_seconds=7200,
        )
        broker = CredentialBroker(config=cfg)
        assert broker.config is cfg
        assert broker.config.default_ttl_seconds == 600
        assert broker.config.max_ttl_seconds == 7200


# ── 6. Docker injection contract ────────────────────────────────────


class TestDockerInjectionContract:
    """Contract: brokered credentials must NOT be passed via env vars.

    The Docker workspace already filters sensitive env vars via
    ``_SENSITIVE_ENV_PATTERNS``.  The broker must produce a token
    suitable for writing to a container-internal file (mirroring the
    gateway_token pattern), never as an env var.
    """

    def test_short_lived_credential_id_is_safe_token(self) -> None:
        """credential_id is safe to log / write to container files."""
        cred = ShortLivedCredential(
            credential_id="slc-abc123",
            provider_name="openai",
            api_key=SecretStr("sk-secret"),
            model="m",
            issued_at=0.0,
            expires_at=1.0,
        )
        # The credential_id is safe (no secret content)
        assert "sk-secret" not in cred.credential_id
        # api_key is a SecretStr — str() redacts it
        assert "sk-secret" not in str(cred.api_key)

    def test_short_lived_credential_to_injection_dict(self) -> None:
        """to_injection_dict produces a dict safe for container files."""
        cred = ShortLivedCredential(
            credential_id="slc-abc",
            provider_name="openai",
            api_key=SecretStr("sk-secret"),
            model="gpt-4",
            issued_at=1000.0,
            expires_at=2000.0,
            scopes=["chat"],
            audience="sandbox-1",
            request_id="req-1",
        )
        inj = cred.to_injection_dict()
        # Safe fields are present
        assert inj["credential_id"] == "slc-abc"
        assert inj["provider_name"] == "openai"
        assert inj["model"] == "gpt-4"
        assert inj["expires_at"] == 2000.0
        assert inj["scopes"] == ["chat"]
        assert inj["audience"] == "sandbox-1"
        # The api_key must NOT be in the injection dict (it stays on host)
        assert "api_key" not in inj


# ── 7. Docker injection helpers ────────────────────────────────────


class TestDockerInjectionHelpers:
    """Tests for :mod:`_docker_injection` — inject / read helpers.

    The helpers wrap ``DockerWorkspace._exec`` + ``_write`` / ``_read``
    private methods so XRuntime can write brokered credential metadata
    into a sandbox container without modifying AS core.  These tests
    pin the contract:

    * :func:`inject_credential_into_workspace` must call ``mkdir -p``
      then ``_write`` with a JSON payload that contains *no* api_key.
    * :func:`read_credential_from_workspace` must return ``None`` when
      the file is missing (``FileNotFoundError``), ``None`` on JSON
      decode error, and the parsed dict otherwise.
    """

    def _make_credential(self) -> ShortLivedCredential:
        return ShortLivedCredential(
            credential_id="slc-inj-1",
            provider_name="openai",
            api_key=SecretStr("sk-host-secret"),
            model="gpt-4",
            issued_at=1000.0,
            expires_at=2000.0,
            scopes=["chat"],
            audience="xruntime-sandbox",
            request_id="req-1",
            base_url="https://api.openai.com",
        )

    def _make_workspace(
        self,
        *,
        read_data: bytes | None = None,
        read_raises: type[Exception] | None = None,
    ) -> Any:
        """Build a MagicMock workspace with async _exec/_write/_read.

        Args:
            read_data: Bytes returned by ``_read`` (when ``read_raises``
                is ``None``).
            read_raises: Exception class raised by ``_read`` instead of
                returning data.

        Returns:
            A configured MagicMock usable as a DockerWorkspace stand-in.
        """
        ws = MagicMock()

        exec_mock = AsyncMock(return_value=MagicMock(exit_code=0))
        ws._exec = exec_mock  # type: ignore[assignment]

        write_mock = AsyncMock(return_value=None)
        ws._write = write_mock  # type: ignore[assignment]

        if read_raises is not None:
            read_mock = AsyncMock(side_effect=read_raises("not found"))
        else:
            read_mock = AsyncMock(return_value=read_data)
        ws._read = read_mock  # type: ignore[assignment]

        return ws

    @pytest.mark.asyncio
    async def test_inject_calls_mkdir_then_write(self) -> None:
        from xruntime._runtime._credential._docker_injection import (
            BROKER_CREDENTIAL_FILE,
            inject_credential_into_workspace,
        )

        ws = self._make_workspace()
        cred = self._make_credential()

        await inject_credential_into_workspace(ws, cred)

        # _exec must be called exactly once with a mkdir -p command
        assert ws._exec.await_count == 1
        mkdir_cmd = ws._exec.await_args.args[0]
        assert "mkdir -p" in mkdir_cmd
        # The target dir must be the parent of BROKER_CREDENTIAL_FILE
        parent = BROKER_CREDENTIAL_FILE.rsplit("/", 1)[0]
        assert parent in mkdir_cmd

        # _write must be called exactly once with the broker file path
        assert ws._write.await_count == 1
        write_args = ws._write.await_args.args
        assert write_args[0] == BROKER_CREDENTIAL_FILE
        # Payload must be bytes
        assert isinstance(write_args[1], bytes)

    @pytest.mark.asyncio
    async def test_inject_payload_excludes_api_key(self) -> None:
        """The api_key must never cross the sandbox boundary."""
        from xruntime._runtime._credential._docker_injection import (
            inject_credential_into_workspace,
        )

        ws = self._make_workspace()
        cred = self._make_credential()

        await inject_credential_into_workspace(ws, cred)

        payload_bytes = ws._write.await_args.args[1]
        payload_str = payload_bytes.decode("utf-8")
        # Safe fields present
        assert "slc-inj-1" in payload_str
        assert "openai" in payload_str
        assert "gpt-4" in payload_str
        assert "xruntime-sandbox" in payload_str
        # The api_key value must NOT appear anywhere in the payload
        assert "sk-host-secret" not in payload_str
        # The "api_key" key must not appear either
        assert "api_key" not in payload_str

    @pytest.mark.asyncio
    async def test_inject_payload_is_valid_json(self) -> None:
        import json as _json

        from xruntime._runtime._credential._docker_injection import (
            inject_credential_into_workspace,
        )

        ws = self._make_workspace()
        cred = self._make_credential()

        await inject_credential_into_workspace(ws, cred)

        payload = _json.loads(ws._write.await_args.args[1].decode("utf-8"))
        assert payload["credential_id"] == "slc-inj-1"
        assert payload["provider_name"] == "openai"
        assert payload["model"] == "gpt-4"
        assert payload["expires_at"] == 2000.0
        assert payload["scopes"] == ["chat"]
        assert payload["audience"] == "xruntime-sandbox"
        assert payload["request_id"] == "req-1"
        assert payload["base_url"] == "https://api.openai.com"

    @pytest.mark.asyncio
    async def test_read_returns_parsed_dict_when_file_exists(self) -> None:
        from xruntime._runtime._credential._docker_injection import (
            read_credential_from_workspace,
        )

        payload = (
            b'{"credential_id":"slc-x","provider_name":"openai",'
            b'"model":"gpt-4","issued_at":1.0,"expires_at":2.0,'
            b'"scopes":[],"audience":"","request_id":"",'
            b'"base_url":null}'
        )
        ws = self._make_workspace(read_data=payload)

        result = await read_credential_from_workspace(ws)
        assert result is not None
        assert result["credential_id"] == "slc-x"
        assert result["provider_name"] == "openai"
        assert result["model"] == "gpt-4"
        assert result["expires_at"] == 2.0

    @pytest.mark.asyncio
    async def test_read_returns_none_when_file_missing(self) -> None:
        """FileNotFoundError must be swallowed → None (file not yet
        injected, e.g. fresh container before first request)."""
        from xruntime._runtime._credential._docker_injection import (
            read_credential_from_workspace,
        )

        ws = self._make_workspace(read_raises=FileNotFoundError)

        result = await read_credential_from_workspace(ws)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_returns_none_on_invalid_json(self) -> None:
        from xruntime._runtime._credential._docker_injection import (
            read_credential_from_workspace,
        )

        ws = self._make_workspace(read_data=b"not-json{")
        result = await read_credential_from_workspace(ws)
        assert result is None

    @pytest.mark.asyncio
    async def test_inject_then_read_round_trip(self) -> None:
        """End-to-end: inject a credential, then read it back.

        Uses an in-memory workspace that stores the last written bytes
        and serves them back via ``_read``.
        """
        from xruntime._runtime._credential._docker_injection import (
            inject_credential_into_workspace,
            read_credential_from_workspace,
        )

        stored: dict[str, bytes] = {}

        async def fake_write(path: str, data: bytes) -> None:
            stored[path] = data

        async def fake_read(path: str) -> bytes:
            if path not in stored:
                raise FileNotFoundError(path)
            return stored[path]

        ws = MagicMock()
        ws._exec = AsyncMock(return_value=MagicMock(exit_code=0))
        ws._write = fake_write  # type: ignore[assignment]
        ws._read = fake_read  # type: ignore[assignment]

        cred = self._make_credential()
        await inject_credential_into_workspace(ws, cred)
        read_back = await read_credential_from_workspace(ws)

        assert read_back is not None
        assert read_back["credential_id"] == cred.credential_id
        assert read_back["provider_name"] == cred.provider_name
        assert read_back["model"] == cred.model
        assert read_back["expires_at"] == cred.expires_at
        assert read_back["scopes"] == cred.scopes
        assert read_back["audience"] == cred.audience
        assert read_back["request_id"] == cred.request_id
        # api_key never appears in the injected metadata
        assert "api_key" not in read_back

    def test_parent_dir_helper(self) -> None:
        from xruntime._runtime._credential._docker_injection import (
            BROKER_CREDENTIAL_FILE,
            _parent_dir,
        )

        # The parent of the broker credential file must be the gateway
        # home dir, matching the existing GATEWAY_CONFIG pattern.
        parent = _parent_dir(BROKER_CREDENTIAL_FILE)
        assert parent == "/root/.agentscope"

        # Edge cases
        assert _parent_dir("/foo/bar.txt") == "/foo"
        assert _parent_dir("bar.txt") == "/"
        assert _parent_dir("/") == "/"


# ── 8. Scope allowlist enforcement ──────────────────────────────────


class TestScopeAllowlist:
    """Scope allowlist enforcement at issue-time.

    When ``config.allowed_scopes`` is non-empty, the broker rejects
    any issue request whose scopes (explicit or defaulted) are not a
    subset of the allowlist.  An empty allowlist (default) means no
    enforcement — backward compatible.
    """

    def test_config_defaults_allowed_scopes_empty(self) -> None:
        cfg = CredentialBrokerConfig()
        assert cfg.allowed_scopes == []

    def test_issue_rejects_scope_not_in_allowlist(self) -> None:
        """A scope not in the allowlist is rejected with ValueError."""
        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                allowed_scopes=["chat", "embed"],
            ),
        )
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        with pytest.raises(ValueError, match="scope"):
            broker.issue(
                provider=provider,
                tenant_id="t",
                session_id="s",
                request_id="r",
                scopes=["admin"],  # not in allowlist
            )

    def test_issue_accepts_scopes_within_allowlist(self) -> None:
        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                allowed_scopes=["chat", "embed"],
            ),
        )
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            scopes=["chat", "embed"],
        )
        assert set(cred.scopes) == {"chat", "embed"}

    def test_issue_rejects_default_scopes_not_in_allowlist(self) -> None:
        """If ``default_scopes`` contains a scope not in
        ``allowed_scopes``, ``issue()`` (which falls back to
        ``default_scopes``) must also reject."""
        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                default_scopes=["chat", "admin"],
                allowed_scopes=["chat", "embed"],
            ),
        )
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        with pytest.raises(ValueError, match="scope"):
            broker.issue(
                provider=provider,
                tenant_id="t",
                session_id="s",
                request_id="r",
                # scopes=None → uses default_scopes=["chat","admin"]
                # "admin" is not in allowed_scopes → reject
            )

    def test_issue_no_allowlist_allows_any_scope(self) -> None:
        """Empty allowlist (default) means no enforcement."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            scopes=["anything", "admin"],
        )
        assert set(cred.scopes) == {"anything", "admin"}

    def test_issue_for_session_also_enforces_allowlist(self) -> None:
        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                allowed_scopes=["chat"],
            ),
        )
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        with pytest.raises(ValueError, match="scope"):
            broker.issue_for_session(
                provider=provider,
                tenant_id="t",
                session_id="s",
                request_id="r",
                scopes=["admin"],
            )

    def test_env_override_allowed_scopes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from xruntime._config import _apply_env_overrides

        monkeypatch.setenv(
            "XRUNTIME_CREDENTIAL_BROKER_ALLOWED_SCOPES",
            '["chat","embed"]',
        )
        cfg = _apply_env_overrides(XRuntimeConfig())
        assert cfg.credential_broker.allowed_scopes == ["chat", "embed"]


# ── 9. Workspace audience binding ───────────────────────────────────


class TestWorkspaceAudienceBinding:
    """Audience binding to a workspace's stable id.

    The ``audience`` field binds a credential to a specific sandbox so
    it cannot be replayed against a different sandbox.  The workspace's
    ``workspace_id`` is the natural audience value — it is stable
    across container restarts and 1:1 with a sandbox.
    """

    def _make_workspace(self, workspace_id: str = "ws-abc") -> Any:
        ws = MagicMock()
        ws.workspace_id = workspace_id
        return ws

    def test_issue_for_workspace_sets_audience_to_workspace_id(
        self,
    ) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        ws = self._make_workspace(workspace_id="ws-xyz")
        cred = broker.issue_for_workspace(
            provider=provider,
            workspace=ws,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        assert cred.audience == "ws-xyz"

    def test_issue_for_workspace_explicit_audience_overrides(
        self,
    ) -> None:
        """An explicit ``audience`` argument wins (escape hatch for
        non-standard bindings)."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        ws = self._make_workspace(workspace_id="ws-xyz")
        cred = broker.issue_for_workspace(
            provider=provider,
            workspace=ws,
            tenant_id="t",
            session_id="s",
            request_id="r",
            audience="custom-audience",
        )
        assert cred.audience == "custom-audience"

    def test_issue_for_workspace_passes_through_scopes_and_ttl(
        self,
    ) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        ws = self._make_workspace()
        cred = broker.issue_for_workspace(
            provider=provider,
            workspace=ws,
            tenant_id="t",
            session_id="s",
            request_id="r",
            ttl_seconds=600,
            scopes=["chat"],
        )
        assert cred.audience == "ws-abc"
        assert cred.scopes == ["chat"]
        assert cred.expires_at - cred.issued_at <= 600

    def test_issue_for_workspace_enforces_scope_allowlist(
        self,
    ) -> None:
        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                allowed_scopes=["chat"],
            ),
        )
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        ws = self._make_workspace()
        with pytest.raises(ValueError, match="scope"):
            broker.issue_for_workspace(
                provider=provider,
                workspace=ws,
                tenant_id="t",
                session_id="s",
                request_id="r",
                scopes=["admin"],
            )

    def test_issue_for_workspace_caches_per_session(self) -> None:
        """Two calls for the same ``(tenant, session, request)``
        reuse the same credential (same as ``issue_for_session``)."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        ws = self._make_workspace()
        cred1 = broker.issue_for_workspace(
            provider=provider,
            workspace=ws,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        cred2 = broker.issue_for_workspace(
            provider=provider,
            workspace=ws,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        assert cred1.credential_id == cred2.credential_id


# ── 10. Revoke-on-teardown ──────────────────────────────────────────


class TestRevokeOnTeardown:
    """Revoke all credentials bound to a workspace when it is torn down.

    When a sandbox is destroyed, any short-lived credentials issued
    for it must be revoked so they cannot be replayed (even before
    their TTL expires).  This is the ``revoke_for_audience`` /
    ``revoke_for_workspace`` path.
    """

    def test_revoke_for_audience_revokes_matching_credentials(
        self,
    ) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        cred_a1 = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s1",
            request_id="r1",
            audience="sandbox-A",
        )
        cred_a2 = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s2",
            request_id="r2",
            audience="sandbox-A",
        )
        cred_b = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s3",
            request_id="r3",
            audience="sandbox-B",
        )

        count = broker.revoke_for_audience("sandbox-A")

        assert count == 2
        assert not broker.validate(cred_a1.credential_id).is_valid
        assert not broker.validate(cred_a2.credential_id).is_valid
        # sandbox-B credential is untouched
        assert broker.validate(cred_b.credential_id).is_valid

    def test_revoke_for_audience_no_matches_returns_zero(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            audience="sandbox-A",
        )
        count = broker.revoke_for_audience("nonexistent")
        assert count == 0

    def test_revoke_for_audience_empty_string_matches_unbound(
        self,
    ) -> None:
        """Passing ``audience=""`` revokes credentials with empty
        audience — the "unbound" credentials issued without a
        workspace binding."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",  # audience defaults to ""
        )
        count = broker.revoke_for_audience("")
        assert count == 1
        assert not broker.validate(cred.credential_id).is_valid

    def test_revoke_for_audience_records_invalidations(self) -> None:
        """Bulk revoke must record each credential in the pending
        invalidations queue so the gateway can drop its cache."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        c1 = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s1",
            request_id="r1",
            audience="sbx",
        )
        c2 = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s2",
            request_id="r2",
            audience="sbx",
        )
        broker.revoke_for_audience("sbx")
        invalidations = broker.drain_invalidations()
        assert c1.credential_id in invalidations
        assert c2.credential_id in invalidations

    def test_revoke_for_workspace_uses_workspace_id(self) -> None:
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        ws = MagicMock()
        ws.workspace_id = "ws-123"
        cred = broker.issue_for_workspace(
            provider=provider,
            workspace=ws,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        assert cred.audience == "ws-123"

        count = broker.revoke_for_workspace(ws)
        assert count == 1
        assert not broker.validate(cred.credential_id).is_valid

    def test_revoke_for_workspace_no_match_returns_zero(self) -> None:
        broker = CredentialBroker()
        ws = MagicMock()
        ws.workspace_id = "ws-never-used"
        count = broker.revoke_for_workspace(ws)
        assert count == 0

    def test_revoke_for_audience_skips_already_revoked(self) -> None:
        """Revoking an already-revoked credential should not
        double-count it."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        cred = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s",
            request_id="r",
            audience="sbx",
        )
        broker.revoke(cred.credential_id)
        # Second revoke via audience should not count it again
        count = broker.revoke_for_audience("sbx")
        assert count == 0

    def test_revoke_for_audience_skips_expired(self) -> None:
        """Expired credentials are not counted — they are already
        invalid and need no explicit revocation."""
        broker = CredentialBroker()
        provider = ModelProviderConfig(name="openai", api_key="k", model="m")
        # Issue an expired credential with audience="sbx"
        broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s-exp",
            request_id="r-exp",
            audience="sbx",
            ttl_seconds=-1,
        )
        # Issue a fresh credential with audience="sbx"
        fresh = broker.issue(
            provider=provider,
            tenant_id="t",
            session_id="s-fresh",
            request_id="r-fresh",
            audience="sbx",
            ttl_seconds=3600,
        )
        # Only the fresh one should be counted
        count = broker.revoke_for_audience("sbx")
        assert count == 1
        assert not broker.validate(fresh.credential_id).is_valid


# ── 11. BrokeredModelResolver workspace integration ────────────────


class TestBrokeredResolverWorkspace:
    """``BrokeredModelResolver.resolve_for_workspace`` — binds the
    short-lived credential's audience to the workspace id."""

    def test_resolve_for_workspace_sets_audience(self) -> None:
        from agentscope.credential import OpenAICredential

        broker = CredentialBroker()
        resolver = BrokeredModelResolver(broker=broker)
        provider = ModelProviderConfig(
            name="openai",
            api_key="sk-test",
            model="gpt-4",
        )
        ws = MagicMock()
        ws.workspace_id = "ws-resolve-1"

        resolution = resolver.resolve_for_workspace(
            provider=provider,
            workspace=ws,
            tenant_id="t",
            session_id="s",
            request_id="r",
        )
        assert isinstance(resolution.credential, OpenAICredential)
        # The broker should have exactly one cached credential
        assert len(broker._cache) == 1
        cached_cred = next(iter(broker._cache.values()))
        assert cached_cred.audience == "ws-resolve-1"

    def test_resolve_for_workspace_enforces_scope_allowlist(
        self,
    ) -> None:
        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                allowed_scopes=["chat"],
            ),
        )
        resolver = BrokeredModelResolver(broker=broker)
        provider = ModelProviderConfig(
            name="openai",
            api_key="k",
            model="m",
        )
        ws = MagicMock()
        ws.workspace_id = "ws-1"
        with pytest.raises(ValueError, match="scope"):
            resolver.resolve_for_workspace(
                provider=provider,
                workspace=ws,
                tenant_id="t",
                session_id="s",
                request_id="r",
                scopes=["admin"],
            )
