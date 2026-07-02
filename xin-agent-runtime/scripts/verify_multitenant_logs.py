# -*- coding: utf-8 -*-
"""Quick multi-tenant log verification script.

Starts XRuntime with Mock model and sends test requests to verify
that multi-tenant isolation logs are printed correctly.
"""
import asyncio
import logging
import sys

# Enable DEBUG logging for tenant isolation
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d - %(message)s",
)

# Silence noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("faker").setLevel(logging.WARNING)
logging.getLogger("asgi_lifespan").setLevel(logging.WARNING)


async def main() -> int:
    """Run multi-tenant log verification."""
    import fakeredis.aioredis
    import httpx
    from asgi_lifespan import LifespanManager

    from agentscope.app import create_app
    from agentscope.app.storage import RedisStorage
    from agentscope.app.message_bus import RedisMessageBus
    from agentscope.app.workspace_manager import LocalWorkspaceManager

    from xruntime._config import XRuntimeConfig, TenantConfig
    from xruntime._gateway._extension import (
        create_xruntime_extension,
        mount_protocol_adapters,
    )
    from xruntime._infra._tenant_storage import TenantAwareRedisStorage
    from xruntime._infra._tenant_message_bus import TenantAwareMessageBus

    print("=" * 70)
    print("Building XRuntime app with multi-tenant config...")
    print("=" * 70)

    # Build config with two tenants
    config = XRuntimeConfig(
        model_providers={
            "mock-model": {
                "name": "mock_chat",
                "api_key": "test-key",
                "model": "mock-v1",
            },
        },
        tenants=[
            TenantConfig(
                id="tenant-alpha",
                name="Alpha Corp",
                tool_allowlist=["Read", "Glob"],
                model_allowlist=["mock-model"],
            ),
            TenantConfig(
                id="tenant-beta",
                name="Beta Inc",
                tool_allowlist=["Read"],
                model_allowlist=["mock-model"],
            ),
        ],
    )
    config.server.auth_enabled = False

    # Simulate server startup log (same format as build_xruntime_app)
    _srv_log = logging.getLogger("xruntime.server")
    _srv_log.info(
        "Multi-tenant isolation enabled: "
        "storage=%s, message_bus=%s, prefix_template=%s, "
        "configured_tenants=%d",
        "TenantAwareRedisStorage",
        "TenantAwareMessageBus",
        config.storage.tenant_prefix,
        len(config.tenants),
    )
    for t in config.tenants:
        tool_list = (
            ",".join(t.tool_allowlist)
            if t.tool_allowlist
            else "all (no restriction)"
        )
        model_list = (
            ",".join(t.model_allowlist)
            if t.model_allowlist
            else "all (no restriction)"
        )
        _srv_log.info(
            "  tenant %s (%s): tools=%s, models=%s",
            t.id,
            t.name or "unnamed",
            tool_list,
            model_list,
        )

    # Use fakeredis for testing
    fr = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Build storage and message bus with fakeredis (same pattern as e2e tests)
    class _FakeRedisStorage(RedisStorage):
        async def __aenter__(self) -> "RedisStorage":
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    class _FakeRedisMessageBus(RedisMessageBus):
        async def __aenter__(self) -> "RedisMessageBus":
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    base_storage = _FakeRedisStorage()
    storage = TenantAwareRedisStorage(
        base_storage,
        config.storage.tenant_prefix,
    )

    base_bus = _FakeRedisMessageBus()
    message_bus = TenantAwareMessageBus(
        base_bus,
        config.storage.tenant_prefix,
    )

    # Create workspace manager
    import tempfile
    import os

    tmp_dir = tempfile.mkdtemp(prefix="xruntime-test-")
    workspace_manager = LocalWorkspaceManager(basedir=os.path.join(tmp_dir, "ws"))

    # Create extension and app
    ext = create_xruntime_extension(config=config)
    app = create_app(
        storage=storage,
        message_bus=message_bus,
        workspace_manager=workspace_manager,
        extra_agent_middlewares=ext["extra_agent_middlewares"],
    )
    mount_protocol_adapters(
        app,
        ext["adapter_registry"],
        config=ext["config"],
        model_resolver=ext["model_resolver"],
    )

    print()
    print("=" * 70)
    print("Starting app with LifespanManager...")
    print("=" * 70)

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=httpx.Timeout(30.0),
        ) as client:
            # Test 1: Send request as tenant-alpha
            print()
            print("=" * 70)
            print("TEST 1: Sending request as tenant-alpha")
            print("=" * 70)

            response = await client.post(
                "/v1/messages",
                json={
                    "model": "mock-model",
                    "messages": [
                        {"role": "user", "content": "Hello from tenant-alpha!"},
                    ],
                    "max_tokens": 1024,
                    "tenant_id": "tenant-alpha",
                    "user_id": "user-001",
                },
            )

            print(f"  Status: {response.status_code}")
            if response.status_code == 200:
                print("  Request succeeded ✓")
            else:
                print(f"  Response body: {response.text[:300]}")

            # Test 2: Send request as tenant-beta
            print()
            print("=" * 70)
            print("TEST 2: Sending request as tenant-beta")
            print("=" * 70)

            response2 = await client.post(
                "/v1/messages",
                json={
                    "model": "mock-model",
                    "messages": [
                        {"role": "user", "content": "Hello from tenant-beta!"},
                    ],
                    "max_tokens": 1024,
                    "tenant_id": "tenant-beta",
                    "user_id": "user-002",
                },
            )

            print(f"  Status: {response2.status_code}")
            if response2.status_code == 200:
                print("  Request succeeded ✓")
            else:
                print(f"  Response body: {response2.text[:300]}")

    await fr.aclose()

    print()
    print("=" * 70)
    print("VERIFICATION COMPLETE")
    print("=" * 70)
    print()
    print("Expected log patterns to verify:")
    print()
    print("  SERVER STARTUP (INFO):")
    print("    ✓ xruntime.server: Multi-tenant isolation enabled")
    print("    ✓ xruntime.server: tenant-alpha (Alpha Corp): tools=Read,Glob, models=mock-model")
    print("    ✓ xruntime.server: tenant-beta (Beta Inc): tools=Read, models=mock-model")
    print()
    print("  REQUEST 1 (tenant-alpha):")
    print("    ✓ xruntime.gateway: Request received: tenant=tenant-alpha, user=user-001")
    print("    ✓ xruntime.gateway: No auth principal, using request-level: tenant=tenant-alpha")
    print("    ✓ xruntime.gateway: current_tenant contextvar set to tenant-alpha")
    print("    ✓ xruntime.gateway.materialize: Resolving model: tenant=tenant-alpha")
    print("    ✓ xruntime.gateway.materialize: Model resolved: tenant=tenant-alpha, provider=mock_chat")
    print("    (DEBUG) xruntime.tenant.storage: Resolving storage key_config for tenant=tenant-alpha")
    print("    (DEBUG) xruntime.tenant.message_bus: Resolved message bus prefix for tenant=tenant-alpha")
    print()
    print("  REQUEST 2 (tenant-beta):")
    print("    ✓ xruntime.gateway: Request received: tenant=tenant-beta, user=user-002")
    print("    ✓ xruntime.gateway: current_tenant contextvar set to tenant-beta")
    print("    ✓ xruntime.gateway.materialize: Model resolved: tenant=tenant-beta")
    print("    (DEBUG) xruntime.tenant.storage: Resolving storage key_config for tenant=tenant-beta")
    print("    (DEBUG) xruntime.tenant.message_bus: Resolved message bus prefix for tenant=tenant-beta")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
