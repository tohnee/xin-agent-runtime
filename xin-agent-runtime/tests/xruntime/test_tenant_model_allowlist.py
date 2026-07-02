# -*- coding: utf-8 -*-
"""Tests for tenant-level model allowlist enforcement.

TDD test file for P1 issue:
"tenant model allowlist 应在 model 解析时强制执行，防止租户使用未授权的模型"
"""
import pytest


class TestTenantModelAllowlist:
    """Tests for tenant-scoped model allowlist."""

    def test_tenant_config_has_model_allowlist(self) -> None:
        """TenantConfig should have a model_allowlist field."""
        from xruntime._config import TenantConfig

        tenant = TenantConfig(
            id="t1",
            name="Test Tenant",
            model_allowlist=["gpt-4", "claude-3-opus"],
        )
        assert set(tenant.model_allowlist) == {"gpt-4", "claude-3-opus"}

    def test_tenant_config_model_allowlist_default_none(self) -> None:
        """model_allowlist should default to None (no restriction)."""
        from xruntime._config import TenantConfig

        tenant = TenantConfig(id="t1", name="Test")
        assert tenant.model_allowlist is None

    def test_model_resolver_respects_tenant_allowlist(self) -> None:
        """ModelResolver.resolve should enforce tenant model allowlist.

        When a model is not in the tenant's allowlist, resolve should
        return None even if the model is globally configured.
        """
        from xruntime._config import XRuntimeConfig, TenantConfig
        from xruntime._runtime._model_resolver import ModelResolver

        # Global config has two providers (dict form)
        config = XRuntimeConfig(
            model_providers={
                "allowed-model": {
                    "name": "openai",
                    "api_key": "test-key",
                    "model": "gpt-4",
                },
                "blocked-model": {
                    "name": "anthropic",
                    "api_key": "test-key-2",
                    "model": "claude-3-opus",
                },
            },
            tenants=[
                TenantConfig(
                    id="t1",
                    name="Tenant 1",
                    model_allowlist=["allowed-model"],
                ),
            ],
        )

        resolver = ModelResolver()

        # Allowed model should resolve
        result = resolver.resolve(
            "allowed-model",
            config=config,
            tenant_id="t1",
        )
        assert result is not None

        # Blocked model should NOT resolve for this tenant
        result = resolver.resolve(
            "blocked-model",
            config=config,
            tenant_id="t1",
        )
        assert result is None

    def test_tenant_allowlist_none_means_all_allowed(self) -> None:
        """When tenant has no model_allowlist, all models are allowed."""
        from xruntime._config import XRuntimeConfig, TenantConfig
        from xruntime._runtime._model_resolver import ModelResolver

        config = XRuntimeConfig(
            model_providers={
                "model-a": {
                    "name": "openai",
                    "api_key": "test-key",
                    "model": "gpt-4",
                },
            },
            tenants=[
                TenantConfig(
                    id="t1",
                    name="Tenant 1",
                    model_allowlist=None,
                ),
            ],
        )

        resolver = ModelResolver()
        result = resolver.resolve(
            "model-a",
            config=config,
            tenant_id="t1",
        )
        assert result is not None

    def test_unknown_tenant_id_treated_as_no_restriction(self) -> None:
        """If tenant_id is not found in config, no restriction applied."""
        from xruntime._config import XRuntimeConfig
        from xruntime._runtime._model_resolver import ModelResolver

        config = XRuntimeConfig(
            model_providers={
                "model-a": {
                    "name": "openai",
                    "api_key": "test-key",
                    "model": "gpt-4",
                },
            },
        )

        resolver = ModelResolver()
        result = resolver.resolve(
            "model-a",
            config=config,
            tenant_id="unknown-tenant",
        )
        # Should still resolve — unknown tenant = no restriction
        assert result is not None

    def test_resolve_provider_enforces_allowlist(self) -> None:
        """resolve_provider should also enforce tenant allowlist."""
        from xruntime._config import XRuntimeConfig, TenantConfig
        from xruntime._runtime._model_resolver import ModelResolver

        config = XRuntimeConfig(
            model_providers={
                "allowed": {
                    "name": "openai",
                    "api_key": "k",
                    "model": "gpt-4",
                },
                "blocked": {
                    "name": "anthropic",
                    "api_key": "k2",
                    "model": "claude",
                },
            },
            tenants=[
                TenantConfig(
                    id="t1",
                    name="T1",
                    model_allowlist=["allowed"],
                ),
            ],
        )

        resolver = ModelResolver()
        assert resolver.resolve_provider("allowed", config, "t1") is not None
        assert resolver.resolve_provider("blocked", config, "t1") is None
