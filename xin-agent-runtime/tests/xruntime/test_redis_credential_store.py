# -*- coding: utf-8 -*-
"""TDD tests for RedisCredentialStore (P3-C Task 2).

Covers Redis-backed credential storage with TTL, multi-tenant
isolation, session indexing, and connection fallback.

Uses ``fakeredis.aioredis.FakeRedis`` for zero-dependency CI tests.
"""
from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import patch

import pytest

from xruntime._runtime._credential._short_lived import ShortLivedCredential
from pydantic import SecretStr


# ── helpers ──────────────────────────────────────────────────────


def _make_credential(
    cred_id: str = "slc-test-1",
    tenant_id: str = "t1",
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


@pytest.fixture
def fake_redis() -> Any:
    """Provide a fakeredis async client."""
    import fakeredis.aioredis

    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
async def store(fake_redis: Any) -> Any:
    """Provide a RedisCredentialStore backed by fakeredis."""
    from xruntime._runtime._credential._redis_store import (
        RedisCredentialStore,
    )

    s = RedisCredentialStore(
        redis_url="redis://localhost:6379/0",
        key_prefix="tenant:{tid}:creds:",
    )
    # Inject fake client (bypass real Redis connection)
    s._client = fake_redis
    return s


# ── 1. Basic CRUD ────────────────────────────────────────────────


class TestRedisCredentialStoreBasicCRUD:
    """RedisCredentialStore — save / load / delete."""

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(self, store: Any) -> None:
        """保存 → 加载,所有字段一致."""
        cred = _make_credential(cred_id="slc-rt-1")
        await store.save(cred, tenant_id="t1")

        loaded = await store.load("slc-rt-1", tenant_id="t1")
        assert loaded is not None
        assert loaded.credential_id == cred.credential_id
        assert loaded.provider_name == cred.provider_name
        assert loaded.model == cred.model
        assert loaded.scopes == cred.scopes
        assert loaded.audience == cred.audience
        assert loaded.api_key.get_secret_value() == "sk-test-secret"

    @pytest.mark.asyncio
    async def test_load_unknown_returns_none(self, store: Any) -> None:
        """加载不存在的 credential_id 返回 None."""
        result = await store.load("slc-nope", tenant_id="t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_removes_credential(self, store: Any) -> None:
        """保存 → 删除 → 加载返回 None."""
        cred = _make_credential(cred_id="slc-del-1")
        await store.save(cred, tenant_id="t1")

        assert await store.delete("slc-del-1", tenant_id="t1") is True
        assert await store.load("slc-del-1", tenant_id="t1") is None

    @pytest.mark.asyncio
    async def test_delete_unknown_returns_false(self, store: Any) -> None:
        """删除不存在的 id 返回 False."""
        assert await store.delete("slc-nope", tenant_id="t1") is False

    @pytest.mark.asyncio
    async def test_save_overwrites_existing(self, store: Any) -> None:
        """同一 credential_id 保存两次,第二次覆盖."""
        cred1 = _make_credential(cred_id="slc-ow-1", scopes=["chat"])
        cred2 = _make_credential(cred_id="slc-ow-1", scopes=["chat", "embed"])

        await store.save(cred1, tenant_id="t1")
        await store.save(cred2, tenant_id="t1")

        loaded = await store.load("slc-ow-1", tenant_id="t1")
        assert loaded is not None
        assert loaded.scopes == ["chat", "embed"]


# ── 2. TTL ───────────────────────────────────────────────────────


class TestRedisCredentialStoreTTL:
    """RedisCredentialStore — TTL expiration."""

    @pytest.mark.asyncio
    async def test_ttl_set_on_save(self, store: Any) -> None:
        """保存 TTL=3600 的凭证,Redis EXPIRE 被设置."""
        cred = _make_credential(cred_id="slc-ttl-1")
        await store.save(cred, tenant_id="t1", ttl_seconds=3600)

        # Redis TTL 应该 > 0
        key = store._cred_key("slc-ttl-1", tenant_id="t1")
        ttl = await store._client.ttl(key)
        assert ttl > 0
        assert ttl <= 3600

    @pytest.mark.asyncio
    async def test_expired_credential_returns_none_on_load(
        self,
        store: Any,
    ) -> None:
        """过期的凭证加载时返回 None."""
        # 保存一个已过期的凭证(TTL=0 立即过期)
        cred = _make_credential(
            cred_id="slc-exp-1",
            expires_at=time.time() - 1.0,
        )
        await store.save(cred, tenant_id="t1", ttl_seconds=0)

        # 等待 Redis 过期( fakeredis 即时过期)
        result = await store.load("slc-exp-1", tenant_id="t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_skips_expired(self, store: Any) -> None:
        """load 跳过已过期的凭证(即使 Redis key 还在)."""
        # 保存凭证,然后模拟 expires_at 已过
        cred = _make_credential(cred_id="slc-sk-1")
        await store.save(cred, tenant_id="t1", ttl_seconds=3600)

        # 手动修改存储的 expires_at 为过去时间
        key = store._cred_key("slc-sk-1", tenant_id="t1")
        raw = await store._client.get(key)
        if raw:
            data = json.loads(raw)
            data["expires_at"] = time.time() - 1.0
            await store._client.set(key, json.dumps(data))

        result = await store.load("slc-sk-1", tenant_id="t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_ttl_none_means_persistent(self, store: Any) -> None:
        """TTL=None 时不设置 Redis EXPIRE."""
        cred = _make_credential(cred_id="slc-persist-1")
        await store.save(cred, tenant_id="t1", ttl_seconds=None)

        key = store._cred_key("slc-persist-1", tenant_id="t1")
        ttl = await store._client.ttl(key)
        assert ttl == -1  # -1 means no expiry set


# ── 3. Multi-tenant isolation ────────────────────────────────────


class TestRedisCredentialStoreMultiTenant:
    """RedisCredentialStore — multi-tenant key isolation."""

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_load_tenant_b_credential(
        self,
        store: Any,
    ) -> None:
        """tenant A 保存的凭证,tenant B 无法加载."""
        cred = _make_credential(cred_id="slc-iso-1")
        await store.save(cred, tenant_id="tenantA")

        # tenant B 尝试加载 → None
        result = await store.load("slc-iso-1", tenant_id="tenantB")
        assert result is None

    @pytest.mark.asyncio
    async def test_key_prefix_includes_tenant_id(self, store: Any) -> None:
        """Redis key 包含 tenant_id 前缀."""
        cred = _make_credential(cred_id="slc-key-1")
        await store.save(cred, tenant_id="t1")

        key = store._cred_key("slc-key-1", tenant_id="t1")
        assert "tenant:t1:" in key
        assert "slc-key-1" in key

    @pytest.mark.asyncio
    async def test_list_by_tenant_returns_only_own(self, store: Any) -> None:
        """list_by_tenant 只返回指定 tenant 的凭证."""
        await store.save(
            _make_credential(cred_id="slc-a-1"),
            tenant_id="tenantA",
        )
        await store.save(
            _make_credential(cred_id="slc-a-2"),
            tenant_id="tenantA",
        )
        await store.save(
            _make_credential(cred_id="slc-b-1"),
            tenant_id="tenantB",
        )

        result = await store.list_by_tenant("tenantA")
        assert len(result) == 2
        ids = {c.credential_id for c in result}
        assert ids == {"slc-a-1", "slc-a-2"}

    @pytest.mark.asyncio
    async def test_delete_by_tenant_only_removes_own(
        self,
        store: Any,
    ) -> None:
        """delete_by_tenant 只删除指定 tenant 的凭证."""
        await store.save(
            _make_credential(cred_id="slc-a-1"),
            tenant_id="tenantA",
        )
        await store.save(
            _make_credential(cred_id="slc-a-2"),
            tenant_id="tenantA",
        )
        await store.save(
            _make_credential(cred_id="slc-b-1"),
            tenant_id="tenantB",
        )

        count = await store.delete_by_tenant("tenantA")
        assert count == 2

        # tenant B 的凭证仍在
        assert await store.load("slc-b-1", tenant_id="tenantB") is not None


# ── 4. Session index ─────────────────────────────────────────────


class TestRedisCredentialStoreSessionIndex:
    """RedisCredentialStore — session tuple indexing."""

    @pytest.mark.asyncio
    async def test_index_by_session_tuple(self, store: Any) -> None:
        """按 (tenant, session, request) 元组索引查找."""
        cred = _make_credential(cred_id="slc-si-1")
        await store.save(
            cred,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )

        found = await store.find_by_session(
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )
        assert found is not None
        assert found.credential_id == "slc-si-1"

    @pytest.mark.asyncio
    async def test_session_index_overwrites_on_reissue(
        self,
        store: Any,
    ) -> None:
        """同一 (t,s,r) 保存两次,索引指向最新."""
        cred1 = _make_credential(cred_id="slc-si-1")
        cred2 = _make_credential(cred_id="slc-si-2")

        await store.save(
            cred1,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )
        await store.save(
            cred2,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )

        found = await store.find_by_session(
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )
        assert found is not None
        assert found.credential_id == "slc-si-2"

    @pytest.mark.asyncio
    async def test_session_index_cleanup_on_delete(self, store: Any) -> None:
        """删除凭证后,session 索引也清除."""
        cred = _make_credential(cred_id="slc-si-3")
        await store.save(
            cred,
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )

        await store.delete("slc-si-3", tenant_id="t1")

        found = await store.find_by_session(
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
        )
        assert found is None


# ── 5. Connection fallback ───────────────────────────────────────


class TestRedisCredentialStoreConnection:
    """RedisCredentialStore — connection error handling."""

    @pytest.mark.asyncio
    async def test_redis_unavailable_raises_connection_error(
        self,
    ) -> None:
        """连接不存在的 Redis 抛 ConnectionError."""
        from xruntime._runtime._credential._redis_store import (
            RedisCredentialStore,
        )

        store = RedisCredentialStore(
            redis_url="redis://localhost:1/0",  # 不可达端口
            key_prefix="tenant:{tid}:creds:",
            connect_timeout=0.5,
        )
        with pytest.raises(Exception):  # noqa: B017
            await store.load("slc-x", tenant_id="t1")

    @pytest.mark.asyncio
    async def test_no_secret_leak_in_redis(self, store: Any) -> None:
        """保存在 Redis 的原始数据不含 api_key 明文."""
        cred = _make_credential(cred_id="slc-leak-1")
        await store.save(cred, tenant_id="t1")

        # 直接读取 Redis 原始值
        key = store._cred_key("slc-leak-1", tenant_id="t1")
        raw = await store._client.get(key)
        assert raw is not None
        raw_str = raw if isinstance(raw, str) else raw.decode("utf-8")

        # api_key 不得出现在 Redis 存储的 JSON 中
        assert "sk-test-secret" not in raw_str
        parsed = json.loads(raw_str)
        # api_key 字段可以存在(以 SecretStr 序列化形式),但不得是明文
        if "api_key" in parsed:
            assert parsed["api_key"] != "sk-test-secret"


# ── 6. Coverage gap fillers (corrupt data + edge cases) ─────────


class TestRedisCredentialStoreSessionTtlExpiry:
    """RedisCredentialStore — session key TTL when all three set."""

    @pytest.mark.asyncio
    async def test_save_with_session_and_request_and_ttl_sets_session_ttl(
        self,
        store: Any,
    ) -> None:
        """save 同时传入 session_id + request_id + ttl_seconds>0
        时,session 索引 key 也设置 Redis EXPIRE."""
        cred = _make_credential(cred_id="slc-sess-ttl-1")
        await store.save(
            cred,
            tenant_id="t1",
            session_id="sTTL",
            request_id="rTTL",
            ttl_seconds=1800,
        )

        sess_key = store._session_key(
            tenant_id="t1",
            session_id="sTTL",
            request_id="rTTL",
        )
        ttl = await store._client.ttl(sess_key)
        assert ttl > 0
        assert ttl <= 1800


class TestRedisCredentialStoreEmptyTenant:
    """RedisCredentialStore — empty tenant edge cases."""

    @pytest.mark.asyncio
    async def test_list_by_tenant_empty_returns_empty_list(
        self,
        store: Any,
    ) -> None:
        """list_by_tenant 在 tenant 无任何凭证时返回空列表."""
        result = await store.list_by_tenant("empty-tenant")
        assert result == []
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_delete_by_tenant_empty_returns_zero(
        self,
        store: Any,
    ) -> None:
        """delete_by_tenant 在 tenant 无任何凭证时返回 0."""
        count = await store.delete_by_tenant("empty-tenant")
        assert count == 0


class TestRedisCredentialStoreFindSessionMiss:
    """RedisCredentialStore — find_by_session miss path."""

    @pytest.mark.asyncio
    async def test_find_by_session_unknown_tuple_returns_none(
        self,
        store: Any,
    ) -> None:
        """find_by_session 在 (t,s,r) 元组从未保存时返回 None."""
        result = await store.find_by_session(
            tenant_id="t1",
            session_id="never",
            request_id="never",
        )
        assert result is None


class TestRedisCredentialStoreDeserializationErrors:
    """RedisCredentialStore — _deserialize corrupt-input handling."""

    @pytest.mark.asyncio
    async def test_load_corrupt_json_returns_none(self, store: Any) -> None:
        """load 在 Redis 中存储的 JSON 损坏时返回 None."""
        # 直接写入损坏的 JSON 到 Redis
        key = store._cred_key("slc-corrupt-json", tenant_id="t1")
        await store._client.set(key, b"not-valid-json{{{")
        # 同时加入 tenant index(否则 load 不到)
        index_key = store._index_key("t1")
        await store._client.sadd(index_key, "slc-corrupt-json")

        result = await store.load("slc-corrupt-json", tenant_id="t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_invalid_base64_api_key_returns_none(
        self,
        store: Any,
    ) -> None:
        """load 在 api_key 声明 base64 编码但内容无效时返回 None."""
        key = store._cred_key("slc-bad-b64", tenant_id="t1")
        # 构造一个 _api_key_encoding=base64 但 api_key 非法的数据
        bad_data = json.dumps(
            {
                "credential_id": "slc-bad-b64",
                "provider_name": "openai",
                "api_key": "!!!not-valid-base64!!!",
                "model": "gpt-4",
                "issued_at": time.time(),
                "expires_at": time.time() + 3600,
                "scopes": ["chat"],
                "audience": "sb-1",
                "request_id": "r1",
                "_api_key_encoding": "base64",
            },
        )
        await store._client.set(key, bad_data)
        await store._client.sadd(store._index_key("t1"), "slc-bad-b64")

        result = await store.load("slc-bad-b64", tenant_id="t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_legacy_plain_string_api_key(self, store: Any) -> None:
        """load 在 api_key 为明文字符串(无 _api_key_encoding 标记)时
        兼容旧数据格式."""
        key = store._cred_key("slc-legacy", tenant_id="t1")
        # 构造一个无 _api_key_encoding 但 api_key 为字符串的旧数据
        legacy_data = json.dumps(
            {
                "credential_id": "slc-legacy",
                "provider_name": "openai",
                "api_key": "sk-legacy-plain",
                "model": "gpt-4",
                "issued_at": time.time(),
                "expires_at": time.time() + 3600,
                "scopes": ["chat"],
                "audience": "sb-1",
                "request_id": "r1",
            },
        )
        await store._client.set(key, legacy_data)
        await store._client.sadd(store._index_key("t1"), "slc-legacy")

        result = await store.load("slc-legacy", tenant_id="t1")
        assert result is not None
        assert result.api_key.get_secret_value() == "sk-legacy-plain"

    @pytest.mark.asyncio
    async def test_load_missing_required_field_returns_none(
        self,
        store: Any,
    ) -> None:
        """load 在 JSON 缺失必需字段时返回 None(构造异常)."""
        key = store._cred_key("slc-missing", tenant_id="t1")
        # 缺失 provider_name / model / expires_at 等必需字段
        bad_data = json.dumps(
            {"credential_id": "slc-missing", "api_key": "abc"},
        )
        await store._client.set(key, bad_data)
        await store._client.sadd(store._index_key("t1"), "slc-missing")

        result = await store.load("slc-missing", tenant_id="t1")
        assert result is None

    def test_deserialize_non_string_raises_type_error_caught(
        self,
    ) -> None:
        """_deserialize 传入非字符串(None / int)时返回 None."""
        from xruntime._runtime._credential._redis_store import (
            RedisCredentialStore,
        )

        # None 触发 TypeError → except (json.JSONDecodeError, TypeError)
        assert RedisCredentialStore._deserialize(None) is None  # type: ignore[arg-type]
        # int 同样触发 TypeError
        assert RedisCredentialStore._deserialize(12345) is None  # type: ignore[arg-type]


class TestRedisCredentialStoreBytesResponse:
    """RedisCredentialStore — bytes response decode path."""

    @pytest.mark.asyncio
    async def test_find_by_session_returns_none_for_bytes_unknown(
        self,
        store: Any,
    ) -> None:
        """find_by_session 在 session key 不存在时返回 None.

        fakeredis with decode_responses=False returns bytes for missing
        keys, but the early-return path on ``None`` is what we test.
        """
        # 没有保存任何 session 索引,直接查询 → None
        result = await store.find_by_session(
            tenant_id="tB",
            session_id="sX",
            request_id="rY",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_load_bytes_payload_round_trip(self, store: Any) -> None:
        """load 在 Redis 返回 bytes 时正确解码."""
        cred = _make_credential(cred_id="slc-bytes-1")
        await store.save(cred, tenant_id="t1")

        # fakeredis with decode_responses=False 默认返回 bytes
        loaded = await store.load("slc-bytes-1", tenant_id="t1")
        assert loaded is not None
        assert loaded.credential_id == "slc-bytes-1"
        assert loaded.api_key.get_secret_value() == "sk-test-secret"
