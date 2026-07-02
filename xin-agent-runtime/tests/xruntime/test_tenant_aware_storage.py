# -*- coding: utf-8 -*-
"""Tests for TenantAwareRedisStorage - per-request multi-tenant isolation.

This is the TDD test file for the P0 issue:
"当前 server 用第一个 tenant 构造 Redis key_config，不是真正 per-request 多租户"
"""
import pytest

from xruntime._infra._tenant import current_tenant


class TestTenantAwareRedisStorage:
    """Tests for TenantAwareRedisStorage — dynamic per-request tenant isolation."""

    def test_import_and_class_exists(self) -> None:
        """TenantAwareRedisStorage class should be importable."""
        from xruntime._infra._tenant_storage import TenantAwareRedisStorage

        assert TenantAwareRedisStorage is not None

    def test_wraps_redis_storage(self) -> None:
        """Should wrap an underlying RedisStorage instance."""
        from unittest.mock import MagicMock

        from xruntime._infra._tenant_storage import TenantAwareRedisStorage

        mock_storage = MagicMock()
        wrapper = TenantAwareRedisStorage(mock_storage, "tenant:{tid}:")

        assert wrapper._storage is mock_storage
        assert wrapper._prefix_template == "tenant:{tid}:"

    @pytest.mark.asyncio
    async def test_uses_current_tenant_for_keys(self) -> None:
        """Each storage call should use the current_tenant context var."""
        from unittest.mock import AsyncMock, MagicMock

        from xruntime._infra._tenant_storage import TenantAwareRedisStorage

        mock_storage = MagicMock()
        mock_storage.upsert_credential = AsyncMock(return_value=True)
        mock_storage.key_config = MagicMock()
        mock_storage.key_config.model_dump.return_value = {
            "credential": "agentscope:cred:{user_id}:{name}",
            "agent": "agentscope:agent:{user_id}:{agent_id}",
        }

        wrapper = TenantAwareRedisStorage(mock_storage, "tenant:{tid}:")

        with current_tenant.tenant("tenant-a"):
            await wrapper.upsert_credential(
                user_id="u1",
                name="test",
                credential_data={"type": "api_key", "api_key": "xxx"},
                provider="openai",
            )

        # Verify the storage was called - the key_config should have been
        # re-prefixed with the current tenant
        assert mock_storage.upsert_credential.called

    @pytest.mark.asyncio
    async def test_different_tenants_isolated(self) -> None:
        """Two tenants operating concurrently should use different key prefixes."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from xruntime._infra._tenant_storage import TenantAwareRedisStorage

        mock_storage = MagicMock()
        mock_storage.upsert_credential = AsyncMock(return_value=True)
        mock_storage.key_config = MagicMock()
        mock_storage.key_config.model_dump.return_value = {
            "credential": "agentscope:cred:{user_id}:{name}",
        }

        wrapper = TenantAwareRedisStorage(mock_storage, "tenant:{tid}:")
        captured_configs: list[str] = []

        async def worker(tenant_id: str) -> None:
            with current_tenant.tenant(tenant_id):
                # Access key_config to trigger tenant prefixing
                cfg = wrapper.key_config
                captured_configs.append(cfg.credential)
                await wrapper.upsert_credential(
                    user_id="u1",
                    name="test",
                    credential_data={"type": "api_key"},
                    provider="openai",
                )

        await asyncio.gather(
            worker("tenant-a"),
            worker("tenant-b"),
        )

        # Both tenants should have different prefixes
        prefixes = set()
        for cfg in captured_configs:
            # Extract the tenant:xxx: prefix
            if cfg.startswith("tenant:"):
                prefix = cfg.split("agentscope:")[0]
                prefixes.add(prefix)

        assert (
            len(prefixes) == 2
        ), f"Expected 2 distinct tenant prefixes, got {prefixes}"

    def test_no_tenant_raises(self) -> None:
        """Accessing key_config without a current tenant should raise."""
        from unittest.mock import MagicMock

        from xruntime._infra._tenant import TenantIsolationError
        from xruntime._infra._tenant_storage import TenantAwareRedisStorage

        mock_storage = MagicMock()
        mock_storage.key_config = MagicMock()
        mock_storage.key_config.model_dump.return_value = {
            "credential": "agentscope:cred:{user_id}:{name}",
        }

        wrapper = TenantAwareRedisStorage(mock_storage, "tenant:{tid}:")

        # Clear any current tenant
        current_tenant.clear()

        with pytest.raises(TenantIsolationError):
            _ = wrapper.key_config

    @pytest.mark.asyncio
    async def test_aenter_aexit_propagated(self) -> None:
        """Context manager methods should be delegated to underlying storage."""
        from unittest.mock import AsyncMock, MagicMock

        from xruntime._infra._tenant_storage import TenantAwareRedisStorage

        mock_storage = MagicMock()
        mock_storage.__aenter__ = AsyncMock(return_value=mock_storage)
        mock_storage.__aexit__ = AsyncMock(return_value=False)

        wrapper = TenantAwareRedisStorage(mock_storage, "tenant:{tid}:")

        async with wrapper as s:
            pass

        mock_storage.__aenter__.assert_awaited_once()
        mock_storage.__aexit__.assert_awaited_once()

    def test_get_client_propagated(self) -> None:
        """get_client should return the underlying client."""
        from unittest.mock import MagicMock

        from xruntime._infra._tenant_storage import TenantAwareRedisStorage

        mock_storage = MagicMock()
        mock_client = MagicMock()
        mock_storage.get_client.return_value = mock_client

        wrapper = TenantAwareRedisStorage(mock_storage, "tenant:{tid}:")

        assert wrapper.get_client() is mock_client
