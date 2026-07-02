# -*- coding: utf-8 -*-
"""TDD tests for P3-C Task 4: Config extension + integration tests.

Two test groups:

1. **Config extension** — :class:`CredentialBrokerConfig` accepts the
   new ``redis_url`` / ``redis_key_prefix`` / ``auto_rotate_*`` /
   ``scope_hierarchy`` fields and round-trips through YAML + env
   overrides.

2. **Cross-module integration** — broker + RedisCredentialStore +
   AutoRotationManager + ScopeHierarchy work together end-to-end:

   * Broker issues a credential with hierarchical scopes.
   * Credential is persisted to RedisCredentialStore (fakeredis).
   * AutoRotationManager sweeps and rotates near-expiry credentials.
   * ScopeHierarchy expands ``admin`` → ``[chat, embed, tool_use]``.
   * Reload from RedisCredentialStore recovers a credential after a
     simulated broker restart.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pydantic import SecretStr

from xruntime._runtime._credential._broker import CredentialBroker
from xruntime._runtime._credential._config import CredentialBrokerConfig
from xruntime._runtime._credential._short_lived import ShortLivedCredential


# ── helpers ──────────────────────────────────────────────────────


def _make_provider() -> Any:
    """Build a fake ModelProviderConfig."""
    fake = MagicMock()
    fake.name = "openai"
    fake.api_key = "sk-test-secret"
    fake.model = "gpt-4"
    fake.base_url = None
    return fake


def _make_credential(
    cred_id: str = "slc-int-1",
    expires_at: float | None = None,
    scopes: list[str] | None = None,
    audience: str = "sb-1",
) -> ShortLivedCredential:
    """Build a test credential."""
    now = time.time()
    return ShortLivedCredential(
        credential_id=cred_id,
        provider_name="openai",
        api_key=SecretStr("sk-test-secret"),
        model="gpt-4",
        issued_at=now,
        expires_at=expires_at if expires_at is not None else now + 3600,
        scopes=scopes or ["chat"],
        audience=audience,
        request_id="req-1",
    )


# ── 1. Config extension ──────────────────────────────────────────


class TestCredentialBrokerConfigExtension:
    """CredentialBrokerConfig — new P3-C fields."""

    def test_default_config_has_new_fields_disabled(self) -> None:
        """默认配置: redis_url=None, auto_rotate_enabled=False,
        scope_hierarchy=空."""
        cfg = CredentialBrokerConfig()
        assert cfg.redis_url is None
        assert cfg.redis_key_prefix == "tenant:{tid}:creds:"
        assert cfg.auto_rotate_enabled is False
        assert cfg.auto_rotate_threshold_seconds == 300
        assert cfg.auto_rotate_check_interval_seconds == 60.0
        assert cfg.scope_hierarchy == {}

    def test_config_with_redis_url(self) -> None:
        """设置 redis_url 启用 Redis 持久化."""
        cfg = CredentialBrokerConfig(
            redis_url="redis://localhost:6379/0",
            redis_key_prefix="tenant:{tid}:creds:",
        )
        assert cfg.redis_url == "redis://localhost:6379/0"
        assert cfg.redis_key_prefix == "tenant:{tid}:creds:"

    def test_config_with_auto_rotate_enabled(self) -> None:
        """设置 auto_rotate_enabled=True 启用自动轮换."""
        cfg = CredentialBrokerConfig(
            auto_rotate_enabled=True,
            auto_rotate_threshold_seconds=120,
            auto_rotate_check_interval_seconds=30.0,
        )
        assert cfg.auto_rotate_enabled is True
        assert cfg.auto_rotate_threshold_seconds == 120
        assert cfg.auto_rotate_check_interval_seconds == 30.0

    def test_config_with_scope_hierarchy(self) -> None:
        """设置 scope_hierarchy 启用层级权限."""
        cfg = CredentialBrokerConfig(
            scope_hierarchy={
                "admin": ["chat", "embed", "tool_use"],
                "tool_use": ["chat"],
            },
        )
        assert "admin" in cfg.scope_hierarchy
        assert "chat" in cfg.scope_hierarchy["admin"]
        assert "embed" in cfg.scope_hierarchy["admin"]
        assert "tool_use" in cfg.scope_hierarchy["admin"]

    def test_config_env_override_redis_url(self) -> None:
        """XRUNTIME_CREDENTIAL_BROKER_REDIS_URL 环境变量覆盖."""
        import os

        from xruntime._config import load_config

        old = os.environ.get("XRUNTIME_CREDENTIAL_BROKER_REDIS_URL")
        try:
            os.environ[
                "XRUNTIME_CREDENTIAL_BROKER_REDIS_URL"
            ] = "redis://env-override:6379/0"
            cfg = load_config(None)
            assert (
                cfg.credential_broker.redis_url
                == "redis://env-override:6379/0"
            )
        finally:
            if old is None:
                os.environ.pop(
                    "XRUNTIME_CREDENTIAL_BROKER_REDIS_URL",
                    None,
                )
            else:
                os.environ["XRUNTIME_CREDENTIAL_BROKER_REDIS_URL"] = old

    def test_config_env_override_auto_rotate_enabled(self) -> None:
        """XRUNTIME_CREDENTIAL_BROKER_AUTO_ROTATE_ENABLED=true 启用."""
        import os

        from xruntime._config import load_config

        old = os.environ.get(
            "XRUNTIME_CREDENTIAL_BROKER_AUTO_ROTATE_ENABLED",
        )
        try:
            os.environ[
                "XRUNTIME_CREDENTIAL_BROKER_AUTO_ROTATE_ENABLED"
            ] = "true"
            cfg = load_config(None)
            assert cfg.credential_broker.auto_rotate_enabled is True
        finally:
            if old is None:
                os.environ.pop(
                    "XRUNTIME_CREDENTIAL_BROKER_AUTO_ROTATE_ENABLED",
                    None,
                )
            else:
                os.environ[
                    "XRUNTIME_CREDENTIAL_BROKER_AUTO_ROTATE_ENABLED"
                ] = old

    def test_config_yaml_load_with_all_new_fields(self) -> None:
        """从 YAML 加载包含所有新字段的配置."""
        import tempfile
        from pathlib import Path

        from xruntime._config import load_config

        yaml_content = """
credential_broker:
  enabled: true
  default_ttl_seconds: 1800
  max_ttl_seconds: 7200
  default_scopes: ["chat"]
  allowed_scopes: ["chat", "embed", "tool_use"]
  cache_max_size: 500
  redis_url: "redis://yaml-host:6379/0"
  redis_key_prefix: "tenant:{tid}:creds:"
  auto_rotate_enabled: true
  auto_rotate_threshold_seconds: 180
  auto_rotate_check_interval_seconds: 15.0
  scope_hierarchy:
    admin: ["chat", "embed", "tool_use"]
    tool_use: ["chat"]
"""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
        ) as f:
            f.write(yaml_content)
            yaml_path = f.name

        try:
            cfg = load_config(yaml_path)
            assert cfg.credential_broker.enabled is True
            assert cfg.credential_broker.redis_url == (
                "redis://yaml-host:6379/0"
            )
            assert cfg.credential_broker.auto_rotate_enabled is True
            assert cfg.credential_broker.auto_rotate_threshold_seconds == 180
            assert (
                cfg.credential_broker.auto_rotate_check_interval_seconds
                == 15.0
            )
            assert "admin" in cfg.credential_broker.scope_hierarchy
            assert "tool_use" in cfg.credential_broker.scope_hierarchy["admin"]
        finally:
            Path(yaml_path).unlink(missing_ok=True)


# ── 2. Cross-module integration ──────────────────────────────────


class TestBrokerWithScopeHierarchyIntegration:
    """Broker + ScopeHierarchy end-to-end."""

    def test_broker_uses_hierarchy_to_validate_required_scopes(
        self,
    ) -> None:
        """broker 用 ScopeHierarchy 验证: admin 凭证满足 chat 需求."""
        from xruntime._runtime._credential._scope_hierarchy import (
            ScopeHierarchy,
        )

        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                enabled=True,
                default_scopes=["admin"],
                allowed_scopes=["admin", "chat", "embed", "tool_use"],
            ),
        )
        hierarchy = ScopeHierarchy(
            {
                "admin": ["chat", "embed", "tool_use"],
                "tool_use": ["chat"],
            },
        )

        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            scopes=["admin"],
        )

        # 凭证本身只有 ["admin"],但用 hierarchy 展开后包含 chat
        assert (
            hierarchy.satisfies(
                granted=cred.scopes,
                required=["chat", "embed"],
            )
            is True
        )
        # admin 不满足 unknown scope
        assert (
            hierarchy.satisfies(
                granted=cred.scopes,
                required=["unknown_scope"],
            )
            is False
        )

    def test_broker_with_hierarchy_handles_cycle_at_construction(
        self,
    ) -> None:
        """ScopeHierarchy 在构造时检测循环并抛 ValueError."""
        from xruntime._runtime._credential._scope_hierarchy import (
            ScopeHierarchy,
        )

        # a → b → a 是循环
        with pytest.raises(ValueError, match="cycle"):
            ScopeHierarchy({"a": ["b"], "b": ["a"]})


class TestBrokerWithRedisStoreIntegration:
    """Broker + RedisCredentialStore persistence integration."""

    @pytest.fixture
    def fake_redis(self) -> Any:
        import fakeredis.aioredis

        return fakeredis.aioredis.FakeRedis()

    @pytest.fixture
    async def store(self, fake_redis: Any) -> Any:
        from xruntime._runtime._credential._redis_store import (
            RedisCredentialStore,
        )

        s = RedisCredentialStore(
            redis_url="redis://localhost:6379/0",
            key_prefix="tenant:{tid}:creds:",
        )
        s._client = fake_redis
        return s

    @pytest.mark.asyncio
    async def test_broker_issue_then_persist_to_redis(
        self,
        store: Any,
    ) -> None:
        """broker.issue 后将凭证保存到 Redis,重新加载仍有效."""
        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                enabled=True,
                default_ttl_seconds=3600,
            ),
        )
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )

        # 保存到 Redis
        await store.save(
            cred,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=3600,
        )

        # 从 Redis 加载
        loaded = await store.load(cred.credential_id, tenant_id="t1")
        assert loaded is not None
        assert loaded.credential_id == cred.credential_id
        assert loaded.api_key.get_secret_value() == "sk-test-secret"

    @pytest.mark.asyncio
    async def test_redis_store_survives_broker_restart(
        self,
        store: Any,
    ) -> None:
        """模拟 broker 重启:旧 broker 保存凭证到 Redis,
        新 broker 从 Redis 恢复."""
        # 第一阶段:旧 broker 签发并保存
        old_broker = CredentialBroker(
            config=CredentialBrokerConfig(enabled=True),
        )
        cred = old_broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )
        await store.save(cred, tenant_id="t1", ttl_seconds=3600)

        # 第二阶段:模拟重启 — 新 broker 不知道这个凭证
        new_broker = CredentialBroker(
            config=CredentialBrokerConfig(enabled=True),
        )
        # 新 broker 内存里没有
        assert new_broker.get(cred.credential_id) is None

        # 但从 Redis 可以恢复
        loaded = await store.load(cred.credential_id, tenant_id="t1")
        assert loaded is not None
        assert loaded.credential_id == cred.credential_id
        # 新 broker 可以把恢复的凭证放回缓存
        new_broker._cache[loaded.credential_id] = loaded
        result = new_broker.validate(loaded.credential_id)
        assert result.is_valid

    @pytest.mark.asyncio
    async def test_redis_store_multi_tenant_isolation_with_broker(
        self,
        store: Any,
    ) -> None:
        """broker + Redis: tenant A 的凭证 tenant B 无法访问."""
        broker = CredentialBroker(
            config=CredentialBrokerConfig(enabled=True),
        )
        cred_a = broker.issue(
            provider=_make_provider(),
            tenant_id="tenantA",
            session_id="s1",
            request_id="r1",
        )
        await store.save(cred_a, tenant_id="tenantA", ttl_seconds=3600)

        # tenant B 尝试加载 → None
        loaded = await store.load(cred_a.credential_id, tenant_id="tenantB")
        assert loaded is None
        # tenant A 可以加载
        loaded = await store.load(cred_a.credential_id, tenant_id="tenantA")
        assert loaded is not None


class TestBrokerWithAutoRotationIntegration:
    """Broker + AutoRotationManager integration."""

    @pytest.mark.asyncio
    async def test_broker_issue_then_auto_rotate(self) -> None:
        """broker 签发凭证 → AutoRotation 轮换 → 旧凭证失效,新凭证生效."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                enabled=True,
                default_ttl_seconds=60,
            ),
        )
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
        )
        old_id = cred.credential_id

        # threshold=3600 让 60 秒 TTL 立即进入轮换窗口
        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        rotated_events: list[tuple[str, Any]] = []
        manager.on_rotate(
            lambda old, new: rotated_events.append((old, new)),
        )

        count = await manager.sweep_once()
        assert count == 1
        assert len(rotated_events) == 1

        old, new = rotated_events[0]
        assert old == old_id
        assert new.credential_id != old_id
        # 旧凭证已撤销
        assert not broker.validate(old_id).is_valid
        # 新凭证有效
        assert broker.validate(new.credential_id).is_valid

    @pytest.mark.asyncio
    async def test_auto_rotation_preserves_scopes_and_audience(
        self,
    ) -> None:
        """轮换后的新凭证保留原始 scopes 和 audience."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = CredentialBroker(
            config=CredentialBrokerConfig(enabled=True),
        )
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
            scopes=["chat", "embed"],
            audience="sb-original",
        )

        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        new_creds: list[Any] = []
        manager.on_rotate(lambda old, new: new_creds.append(new))

        await manager.sweep_once()
        assert len(new_creds) == 1
        new = new_creds[0]
        assert new.scopes == ["chat", "embed"]
        assert new.audience == "sb-original"
        assert new.provider_name == "openai"
        assert new.model == "gpt-4"


class TestFullStackIntegration:
    """Full stack: Broker + Redis + AutoRotation + ScopeHierarchy."""

    @pytest.fixture
    def fake_redis(self) -> Any:
        import fakeredis.aioredis

        return fakeredis.aioredis.FakeRedis()

    @pytest.fixture
    async def store(self, fake_redis: Any) -> Any:
        from xruntime._runtime._credential._redis_store import (
            RedisCredentialStore,
        )

        s = RedisCredentialStore(
            redis_url="redis://localhost:6379/0",
            key_prefix="tenant:{tid}:creds:",
        )
        s._client = fake_redis
        return s

    @pytest.mark.asyncio
    async def test_full_stack_issue_persist_rotate_recover(
        self,
        store: Any,
    ) -> None:
        """端到端:签发 → 持久化 → 轮换 → 从 Redis 恢复新凭证."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )
        from xruntime._runtime._credential._scope_hierarchy import (
            ScopeHierarchy,
        )

        # 配置 broker + hierarchy
        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                enabled=True,
                default_ttl_seconds=60,
                default_scopes=["admin"],
                allowed_scopes=["admin", "chat", "embed", "tool_use"],
                scope_hierarchy={
                    "admin": ["chat", "embed", "tool_use"],
                    "tool_use": ["chat"],
                },
            ),
        )
        hierarchy = ScopeHierarchy(
            broker.config.scope_hierarchy,
        )

        # 1. 签发凭证(admin scope,包含隐式 chat/embed/tool_use)
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
            scopes=["admin"],
        )
        old_id = cred.credential_id

        # 验证 hierarchy 展开正确
        expanded = hierarchy.expand(["admin"])
        assert "chat" in expanded
        assert "embed" in expanded
        assert "tool_use" in expanded

        # 2. 持久化到 Redis
        await store.save(
            cred,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
        )

        # 3. 启动 AutoRotation 并立即触发轮换
        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        new_creds: list[Any] = []
        manager.on_rotate(lambda old, new: new_creds.append(new))

        rotated = await manager.sweep_once()
        assert rotated == 1
        assert len(new_creds) == 1
        new_cred = new_creds[0]

        # 4. 旧凭证已撤销 + 从 Redis 加载应被清理或返回 None
        assert not broker.validate(old_id).is_valid

        # 5. 新凭证可以持久化到 Redis 并恢复
        await store.save(
            new_cred,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
        )
        loaded = await store.load(new_cred.credential_id, tenant_id="t1")
        assert loaded is not None
        assert loaded.credential_id == new_cred.credential_id
        assert loaded.scopes == ["admin"]
        # hierarchy 仍然适用于新凭证
        assert (
            hierarchy.satisfies(
                granted=loaded.scopes,
                required=["chat", "embed"],
            )
            is True
        )

    @pytest.mark.asyncio
    async def test_full_stack_multi_tenant_with_hierarchy(
        self,
        store: Any,
    ) -> None:
        """多租户: tenant A (admin) 和 tenant B (chat only) 隔离."""
        from xruntime._runtime._credential._scope_hierarchy import (
            ScopeHierarchy,
        )

        broker = CredentialBroker(
            config=CredentialBrokerConfig(
                enabled=True,
                allowed_scopes=["admin", "chat", "embed"],
            ),
        )
        hierarchy = ScopeHierarchy(
            {"admin": ["chat", "embed"]},
        )

        # tenant A: admin scope
        cred_a = broker.issue(
            provider=_make_provider(),
            tenant_id="tenantA",
            session_id="sA",
            request_id="rA",
            scopes=["admin"],
        )
        # tenant B: chat only
        cred_b = broker.issue(
            provider=_make_provider(),
            tenant_id="tenantB",
            session_id="sB",
            request_id="rB",
            scopes=["chat"],
        )

        await store.save(cred_a, tenant_id="tenantA", ttl_seconds=3600)
        await store.save(cred_b, tenant_id="tenantB", ttl_seconds=3600)

        # tenant A 的凭证满足 chat + embed(admin 隐式授予)
        assert (
            hierarchy.satisfies(
                granted=cred_a.scopes,
                required=["chat", "embed"],
            )
            is True
        )
        # tenant B 的凭证只满足 chat,不满足 embed
        assert (
            hierarchy.satisfies(
                granted=cred_b.scopes,
                required=["chat"],
            )
            is True
        )
        assert (
            hierarchy.satisfies(
                granted=cred_b.scopes,
                required=["embed"],
            )
            is False
        )

        # 多租户隔离: tenant A 不能从 Redis 加载 tenant B 的凭证
        assert (
            await store.load(
                cred_b.credential_id,
                tenant_id="tenantA",
            )
            is None
        )
        assert (
            await store.load(
                cred_a.credential_id,
                tenant_id="tenantB",
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_full_stack_config_driven_construction(self) -> None:
        """从 CredentialBrokerConfig 构建完整 stack."""
        cfg = CredentialBrokerConfig(
            enabled=True,
            default_ttl_seconds=1800,
            default_scopes=["chat"],
            allowed_scopes=["chat", "embed", "admin"],
            auto_rotate_enabled=True,
            auto_rotate_threshold_seconds=120,
            auto_rotate_check_interval_seconds=30.0,
            scope_hierarchy={"admin": ["chat", "embed"]},
        )

        # 从 config 构建各组件
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )
        from xruntime._runtime._credential._scope_hierarchy import (
            ScopeHierarchy,
        )

        broker = CredentialBroker(config=cfg)
        assert broker.config.redis_url is None  # 默认无 Redis
        assert broker.config.auto_rotate_enabled is True

        hierarchy = ScopeHierarchy(cfg.scope_hierarchy)
        assert (
            hierarchy.satisfies(
                granted=["admin"],
                required=["chat", "embed"],
            )
            is True
        )

        policy = AutoRotationPolicy(
            threshold_seconds=cfg.auto_rotate_threshold_seconds,
        )
        assert policy.threshold_seconds == 120

        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=(cfg.auto_rotate_check_interval_seconds),
        )
        assert manager._interval == 30.0


# ── 3. Backward compatibility ────────────────────────────────────


class TestCredentialBrokerConfigBackwardCompat:
    """CredentialBrokerConfig — old code without new fields still works."""

    def test_old_style_construction_still_works(self) -> None:
        """旧式构造(只传 enabled + ttl)仍然有效,新字段使用默认值."""
        cfg = CredentialBrokerConfig(
            enabled=True,
            default_ttl_seconds=3600,
            max_ttl_seconds=86400,
        )
        # 新字段使用默认值
        assert cfg.redis_url is None
        assert cfg.auto_rotate_enabled is False
        assert cfg.scope_hierarchy == {}

    def test_broker_with_old_style_config_still_works(self) -> None:
        """旧式 config 构造的 broker 仍然可以签发凭证."""
        cfg = CredentialBrokerConfig(
            enabled=True,
            default_ttl_seconds=3600,
            default_scopes=["chat"],
        )
        broker = CredentialBroker(config=cfg)
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )
        assert cred.credential_id.startswith("slc-")
        assert cred.scopes == ["chat"]
        assert broker.validate(cred.credential_id).is_valid
