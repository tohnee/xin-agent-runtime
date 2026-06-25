# -*- coding: utf-8 -*-
"""Tests for multi-tenant storage and message bus isolation."""
import pytest

from xruntime._infra._tenant import (
    TenantKeyPrefixer,
    TenantContext,
    TenantIsolationError,
)


class TestTenantKeyPrefixer:
    """Tests for Redis key prefix-based tenant isolation."""

    def test_prefix_format(self) -> None:
        """Prefix should contain {tid} placeholder."""
        prefixer = TenantKeyPrefixer("tenant:{tid}:")
        assert prefixer.prefix_template == "tenant:{tid}:"

    def test_apply_prefix(self) -> None:
        """Should prepend tenant prefix to keys."""
        prefixer = TenantKeyPrefixer("tenant:{tid}:")
        key = prefixer.apply("acme", "agentscope:user:u1:agent:a1")
        assert key == "tenant:acme:agentscope:user:u1:agent:a1"

    def test_different_tenants_different_keys(self) -> None:
        """Same base key with different tenants should differ."""
        prefixer = TenantKeyPrefixer("tenant:{tid}:")
        base = "agentscope:user:u1:session:s1"
        key_a = prefixer.apply("tenant-a", base)
        key_b = prefixer.apply("tenant-b", base)
        assert key_a != key_b
        assert "tenant-a" in key_a
        assert "tenant-b" in key_b

    def test_empty_tenant_raises(self) -> None:
        """Empty tenant id should raise."""
        prefixer = TenantKeyPrefixer("tenant:{tid}:")
        with pytest.raises(TenantIsolationError):
            prefixer.apply("", "some:key")

    def test_none_tenant_raises(self) -> None:
        """None tenant id should raise."""
        prefixer = TenantKeyPrefixer("tenant:{tid}:")
        with pytest.raises(TenantIsolationError):
            prefixer.apply(None, "some:key")  # type: ignore[arg-type]


class TestTenantContext:
    """Tests for TenantContext — context-var-scoped tenant scope."""

    def test_set_and_get(self) -> None:
        """Should set and get current tenant."""
        ctx = TenantContext()
        ctx.set("acme")
        assert ctx.get() == "acme"

    def test_default_tenant(self) -> None:
        """Default tenant should be 'default'."""
        ctx = TenantContext()
        assert ctx.get() == "default"

    def test_custom_default_tenant(self) -> None:
        """Custom default tenant should be used."""
        ctx = TenantContext(default_tenant="base")
        assert ctx.get() == "base"

    def test_clear(self) -> None:
        """Clear should reset to default."""
        ctx = TenantContext()
        ctx.set("acme")
        ctx.clear()
        assert ctx.get() == "default"

    def test_context_manager(self) -> None:
        """Should work as a context manager."""
        ctx = TenantContext()
        with ctx.tenant("acme"):
            assert ctx.get() == "acme"
        assert ctx.get() == "default"

    def test_nested_context_manager(self) -> None:
        """Nested context managers should restore correctly."""
        ctx = TenantContext()
        with ctx.tenant("outer"):
            assert ctx.get() == "outer"
            with ctx.tenant("inner"):
                assert ctx.get() == "inner"
            assert ctx.get() == "outer"
        assert ctx.get() == "default"

    async def test_async_isolation_between_tasks(self) -> None:
        """Concurrent async tasks should NOT see each other's tenant.

        This is the regression test for Bug 2: the old instance-attr
        implementation let one task's ``set()`` clobber another's.
        """
        import asyncio
        ctx = TenantContext()
        results: dict[str, str] = {}

        async def worker(tenant: str, delay: float) -> None:
            ctx.set(tenant)
            await asyncio.sleep(delay)
            # Even though another task called set() in between,
            # this task must still see its own tenant.
            results[tenant] = ctx.get()

        await asyncio.gather(
            worker("tenant-a", 0.01),
            worker("tenant-b", 0.005),
        )

        assert results["tenant-a"] == "tenant-a"
        assert results["tenant-b"] == "tenant-b"

    async def test_context_manager_restores_after_await(self) -> None:
        """Context manager should restore tenant after an await."""
        import asyncio
        ctx = TenantContext()
        ctx.set("before")

        async def inner() -> None:
            with ctx.tenant("scoped"):
                await asyncio.sleep(0.001)
                assert ctx.get() == "scoped"

        await inner()
        assert ctx.get() == "before"

    def test_thread_isolation(self) -> None:
        """Each thread should have its own tenant context.

        Another regression test for Bug 2.
        """
        import threading
        ctx = TenantContext()
        errors: list[str] = []

        def worker(tenant: str) -> None:
            ctx.set(tenant)
            import time
            time.sleep(0.01)
            got = ctx.get()
            if got != tenant:
                errors.append(
                    f"expected {tenant}, got {got}",
                )

        threads = [
            threading.Thread(target=worker, args=("t1",)),
            threading.Thread(target=worker, args=("t2",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


class TestTenantIsolationScenario:
    """End-to-end tenant isolation scenarios."""

    def test_two_tenants_same_user_isolated(self) -> None:
        """Two tenants with same user_id should have different keys."""
        prefixer = TenantKeyPrefixer("tenant:{tid}:")
        base_key = "agentscope:user:alice:agent:coder"

        tenant_a_key = prefixer.apply("company-a", base_key)
        tenant_b_key = prefixer.apply("company-b", base_key)

        assert tenant_a_key != tenant_b_key
        assert "company-a" in tenant_a_key
        assert "company-b" in tenant_b_key

    def test_tenant_namespace_not_leaking(self) -> None:
        """Prefixed key should not contain other tenant's id."""
        prefixer = TenantKeyPrefixer("tenant:{tid}:")
        key = prefixer.apply("acme", "agentscope:user:alice:agent:c1")
        assert "tenant:acme:" in key
        assert "company-b" not in key


class TestBuildTenantKeyConfig:
    """Tests for build_tenant_key_config (issue #15)."""

    def test_all_templates_prefixed(self) -> None:
        """Every key template gets the tenant prefix."""
        from xruntime._infra._tenant import build_tenant_key_config

        cfg = build_tenant_key_config("acme", "tenant:{tid}:")
        data = cfg.model_dump()
        assert data["credential"].startswith("tenant:acme:")
        assert data["agent"].startswith("tenant:acme:")
        assert data["session"].startswith("tenant:acme:")
        assert data["messages"].startswith("tenant:acme:")

    def test_distinct_tenants_distinct_prefixes(self) -> None:
        """Two tenants produce non-overlapping key namespaces."""
        from xruntime._infra._tenant import build_tenant_key_config

        a = build_tenant_key_config("a", "tenant:{tid}:")
        b = build_tenant_key_config("b", "tenant:{tid}:")
        assert a.agent != b.agent
        assert a.agent.startswith("tenant:a:")
        assert b.agent.startswith("tenant:b:")

    def test_empty_tenant_raises(self) -> None:
        """An empty tenant id raises TenantIsolationError."""
        from xruntime._infra._tenant import build_tenant_key_config

        with pytest.raises(TenantIsolationError):
            build_tenant_key_config("", "tenant:{tid}:")

    def test_produces_valid_redis_key_config(self) -> None:
        """The result is a usable RedisStorage.KeyConfig instance."""
        from agentscope.app.storage import RedisStorage
        from xruntime._infra._tenant import build_tenant_key_config

        cfg = build_tenant_key_config("acme", "tenant:{tid}:")
        assert isinstance(cfg, RedisStorage.KeyConfig)


class TestCurrentTenantSingleton:
    """Tests for the process-wide current_tenant context (issue #14)."""

    def test_current_tenant_exists(self) -> None:
        """A module-level current_tenant TenantContext is exported."""
        from xruntime._infra._tenant import current_tenant, TenantContext

        assert isinstance(current_tenant, TenantContext)

    def test_request_handler_imports_current_tenant(self) -> None:
        """The gateway sets current_tenant from the request (issue #14)."""
        import inspect
        from xruntime._gateway import _extension

        source = inspect.getsource(_extension)
        assert "current_tenant.set(" in source
