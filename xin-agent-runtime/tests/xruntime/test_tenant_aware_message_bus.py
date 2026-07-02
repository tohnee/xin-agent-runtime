# -*- coding: utf-8 -*-
"""Tests for tenant-aware MessageBus isolation.

TDD test file for P0 issue:
"MessageBus 也加 tenant prefix，实现多租户事件隔离"
"""
import pytest


class TestTenantAwareMessageBus:
    """Tests for tenant-scoped MessageBus key prefixing."""

    @pytest.mark.asyncio
    async def test_session_events_key_has_tenant_prefix(self) -> None:
        """Session event keys should include tenant prefix."""
        from unittest.mock import AsyncMock, MagicMock

        from xruntime._infra._tenant import current_tenant
        from xruntime._infra._tenant_message_bus import (
            TenantAwareMessageBus,
        )

        mock_bus = MagicMock()
        mock_bus.log_append = AsyncMock(return_value="1234-0")
        mock_bus.publish = AsyncMock()

        bus = TenantAwareMessageBus(mock_bus, prefix_template="tenant:{tid}:")

        with current_tenant.tenant("t1"):
            await bus.session_publish_event(
                session_id="s1",
                event={"type": "REPLY_END"},
            )

        # log_append should have been called with a tenant-prefixed key
        call_args = mock_bus.log_append.call_args
        key = call_args[0][0]
        assert key.startswith("tenant:t1:")
        assert "s1" in key

    @pytest.mark.asyncio
    async def test_different_tenants_isolated(self) -> None:
        """Different tenants should have different key prefixes."""
        from unittest.mock import AsyncMock, MagicMock

        from xruntime._infra._tenant import current_tenant
        from xruntime._infra._tenant_message_bus import (
            TenantAwareMessageBus,
        )

        mock_bus = MagicMock()
        mock_bus.log_append = AsyncMock(return_value="1234-0")
        mock_bus.publish = AsyncMock()

        bus = TenantAwareMessageBus(mock_bus, prefix_template="tenant:{tid}:")

        # Tenant 1
        with current_tenant.tenant("t1"):
            await bus.session_publish_event(
                session_id="s1",
                event={"type": "REPLY_END"},
            )
        key_t1 = mock_bus.log_append.call_args[0][0]
        mock_bus.reset_mock()
        mock_bus.log_append = AsyncMock(return_value="5678-0")
        mock_bus.publish = AsyncMock()

        # Tenant 2
        with current_tenant.tenant("t2"):
            await bus.session_publish_event(
                session_id="s1",
                event={"type": "REPLY_END"},
            )
        key_t2 = mock_bus.log_append.call_args[0][0]

        # Keys should be different due to different tenant prefixes
        assert key_t1 != key_t2
        assert "tenant:t1:" in key_t1
        assert "tenant:t2:" in key_t2

    @pytest.mark.asyncio
    async def test_no_tenant_raises_error(self) -> None:
        """Accessing without tenant context should raise an error."""
        from unittest.mock import MagicMock

        from xruntime._infra._tenant_message_bus import (
            TenantAwareMessageBus,
            TenantIsolationError,
        )

        mock_bus = MagicMock()
        bus = TenantAwareMessageBus(mock_bus, prefix_template="tenant:{tid}:")

        with pytest.raises(TenantIsolationError):
            await bus.session_publish_event(
                session_id="s1",
                event={"type": "REPLY_END"},
            )

    @pytest.mark.asyncio
    async def test_session_subscribe_uses_tenant_prefix(self) -> None:
        """Subscribe should also use tenant-prefixed keys."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from xruntime._infra._tenant import current_tenant
        from xruntime._infra._tenant_message_bus import (
            TenantAwareMessageBus,
        )

        mock_bus = MagicMock()

        async def _fake_subscribe(key, on_ready=None):
            yield {"type": "REPLY_END", "_entry_id": "1-0"}

        mock_bus.subscribe = _fake_subscribe

        bus = TenantAwareMessageBus(mock_bus, prefix_template="tenant:{tid}:")

        captured_keys = []

        async def _track_subscribe(key, on_ready=None):
            captured_keys.append(key)
            yield {"type": "REPLY_END", "_entry_id": "1-0"}

        mock_bus.subscribe = _track_subscribe

        with current_tenant.tenant("t1"):
            async for evt in bus.session_subscribe_events(session_id="s1"):
                break

        assert len(captured_keys) == 1
        assert captured_keys[0].startswith("tenant:t1:")
        assert "s1" in captured_keys[0]

    def test_wrapper_does_not_delegate_unknown_public_attrs(self) -> None:
        """Wrapper must NOT delegate unknown public attributes to the
        inner bus — doing so would bypass the tenant prefix.

        Fail closed: ``__getattr__`` raises ``AttributeError`` for any
        public attribute that was not explicitly wrapped on
        ``TenantAwareMessageBus``.
        """
        from unittest.mock import MagicMock

        from xruntime._infra._tenant_message_bus import (
            TenantAwareMessageBus,
        )

        mock_bus = MagicMock()
        mock_bus.some_custom_attr = 42

        bus = TenantAwareMessageBus(mock_bus, prefix_template="tenant:{tid}:")

        with pytest.raises(AttributeError):
            bus.some_custom_attr

        # Sanity: the inner bus still has the attribute — the wrapper
        # is refusing to expose it, not shadowing it.
        assert mock_bus.some_custom_attr == 42
