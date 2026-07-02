# -*- coding: utf-8 -*-
"""TDD tests for AutoRotation (P3-C Task 3).

Covers proactive credential rotation: a policy decides when a
credential is "near expiry" and a background manager sweeps the
broker cache, firing ``on_rotate`` callbacks so dependents can
refresh their references before the credential actually expires.

Components under test:

* :class:`AutoRotationPolicy` — pure function ``should_rotate(cred,
  now)`` based on remaining TTL vs. threshold.
* :class:`AutoRotationManager` — async background sweeper that
  periodically scans the broker cache, finds rotation candidates,
  revokes + reissues them, and fires registered callbacks.

The manager does **not** itself issue credentials — it delegates to
the broker so all issuance still flows through the single chokepoint.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xruntime._runtime._credential._broker import CredentialBroker
from xruntime._runtime._credential._config import CredentialBrokerConfig
from xruntime._runtime._credential._short_lived import ShortLivedCredential
from pydantic import SecretStr


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
    cred_id: str = "slc-ar-1",
    expires_at: float | None = None,
    scopes: list[str] | None = None,
    audience: str = "sb-1",
    request_id: str = "req-1",
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
        request_id=request_id,
    )


def _make_broker(
    default_ttl: int = 3600,
    max_ttl: int = 86400,
    cache_max_size: int = 1000,
) -> CredentialBroker:
    """Build a CredentialBroker with the given config."""
    return CredentialBroker(
        config=CredentialBrokerConfig(
            enabled=True,
            default_ttl_seconds=default_ttl,
            max_ttl_seconds=max_ttl,
            cache_max_size=cache_max_size,
        ),
    )


def _issue_credential(
    broker: CredentialBroker,
    *,
    cred_id: str = "slc-ar-1",
    tenant_id: str = "t1",
    session_id: str = "s1",
    request_id: str = "r1",
    ttl_seconds: int | None = None,
    scopes: list[str] | None = None,
    audience: str = "sb-1",
) -> ShortLivedCredential:
    """Issue a credential via the broker with a fixed credential_id.

    Uses ``patch`` on ``uuid.uuid4`` so the id is deterministic.
    """
    import uuid as uuid_mod

    fake_uuid = MagicMock()
    fake_uuid.hex = cred_id.replace("slc-", "")
    with patch.object(uuid_mod, "uuid4", return_value=fake_uuid):
        return broker.issue(
            provider=_make_provider(),
            tenant_id=tenant_id,
            session_id=session_id,
            request_id=request_id,
            ttl_seconds=ttl_seconds,
            scopes=scopes,
            audience=audience,
        )


# ── 1. AutoRotationPolicy ────────────────────────────────────────


class TestAutoRotationPolicy:
    """AutoRotationPolicy — should_rotate decision logic."""

    def test_policy_should_rotate_when_remaining_ttl_below_threshold(
        self,
    ) -> None:
        """剩余 TTL < threshold 时应触发轮换."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationPolicy,
        )

        policy = AutoRotationPolicy(threshold_seconds=300)
        now = time.time()
        # 凭证将在 100 秒后过期,threshold=300 → 应轮换
        cred = _make_credential(expires_at=now + 100)
        assert policy.should_rotate(cred, now=now) is True

    def test_policy_should_not_rotate_when_remaining_ttl_above_threshold(
        self,
    ) -> None:
        """剩余 TTL > threshold 时不应触发轮换."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationPolicy,
        )

        policy = AutoRotationPolicy(threshold_seconds=300)
        now = time.time()
        # 凭证将在 1000 秒后过期,threshold=300 → 不应轮换
        cred = _make_credential(expires_at=now + 1000)
        assert policy.should_rotate(cred, now=now) is False

    def test_policy_should_rotate_when_already_expired(self) -> None:
        """凭证已过期时应触发轮换(虽然通常会被清理)."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationPolicy,
        )

        policy = AutoRotationPolicy(threshold_seconds=300)
        now = time.time()
        cred = _make_credential(expires_at=now - 10)
        assert policy.should_rotate(cred, now=now) is True

    def test_policy_at_exact_threshold_boundary(self) -> None:
        """剩余 TTL 恰好等于 threshold 时不应轮换(严格小于)."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationPolicy,
        )

        policy = AutoRotationPolicy(threshold_seconds=300)
        now = time.time()
        # 剩余 TTL 恰好 300 秒 → 不应轮换(严格小于才轮换)
        cred = _make_credential(expires_at=now + 300)
        assert policy.should_rotate(cred, now=now) is False

    def test_policy_next_rotation_at_returns_check_time(self) -> None:
        """next_rotation_at 返回下次应检查的时间(remaining - threshold)."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationPolicy,
        )

        policy = AutoRotationPolicy(threshold_seconds=300)
        now = time.time()
        # 凭证 1000 秒后过期,threshold=300 → 在 700 秒后进入轮换窗口
        cred = _make_credential(expires_at=now + 1000)
        next_at = policy.next_rotation_at(cred, now=now)
        # 下次轮换时间应该在 (now + 700) 附近(允许浮点误差)
        assert abs(next_at - (now + 700)) < 1.0

    def test_policy_zero_threshold_never_rotates_unless_expired(
        self,
    ) -> None:
        """threshold=0 时仅在已过期时轮换(边界)."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationPolicy,
        )

        policy = AutoRotationPolicy(threshold_seconds=0)
        now = time.time()
        # 1 秒后过期,threshold=0,严格小于 0 不成立 → 不轮换
        cred = _make_credential(expires_at=now + 1)
        assert policy.should_rotate(cred, now=now) is False
        # 已过期 → 轮换
        cred_expired = _make_credential(expires_at=now - 1)
        assert policy.should_rotate(cred_expired, now=now) is True

    def test_policy_negative_threshold_raises_value_error(self) -> None:
        """threshold < 0 时抛 ValueError."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationPolicy,
        )

        with pytest.raises(ValueError, match="threshold_seconds"):
            AutoRotationPolicy(threshold_seconds=-1)

    def test_policy_threshold_seconds_property(self) -> None:
        """threshold_seconds 属性返回构造时传入的值."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationPolicy,
        )

        policy = AutoRotationPolicy(threshold_seconds=42)
        assert policy.threshold_seconds == 42

    def test_policy_should_rotate_default_now_uses_time_time(
        self,
    ) -> None:
        """should_rotate 不传 now 时使用 time.time()."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationPolicy,
        )

        policy = AutoRotationPolicy(threshold_seconds=300)
        # 凭证已过期(不传 now,使用真实当前时间)
        cred = _make_credential(expires_at=time.time() - 100)
        assert policy.should_rotate(cred) is True
        # 凭证远未过期
        cred_future = _make_credential(expires_at=time.time() + 10000)
        assert policy.should_rotate(cred_future) is False


# ── 2. AutoRotationManager lifecycle ─────────────────────────────


class TestAutoRotationManagerLifecycle:
    """AutoRotationManager — start / stop / idempotency."""

    @pytest.mark.asyncio
    async def test_start_creates_background_task(self) -> None:
        """start() 创建后台 asyncio Task."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        manager = AutoRotationManager(
            broker=broker,
            policy=AutoRotationPolicy(threshold_seconds=300),
            check_interval_seconds=1,
        )
        assert manager._task is None
        await manager.start()
        assert manager._task is not None
        assert not manager._task.done()
        await manager.stop()
        assert manager._task is None

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        """多次 start() 不创建多个 task."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        manager = AutoRotationManager(
            broker=broker,
            policy=AutoRotationPolicy(threshold_seconds=300),
            check_interval_seconds=1,
        )
        await manager.start()
        task1 = manager._task
        await manager.start()
        task2 = manager._task
        assert task1 is task2
        await manager.stop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self) -> None:
        """stop() 在未启动时也是安全的."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        manager = AutoRotationManager(
            broker=broker,
            policy=AutoRotationPolicy(threshold_seconds=300),
            check_interval_seconds=1,
        )
        # 未启动直接 stop — 不抛异常
        await manager.stop()
        assert manager._task is None

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self) -> None:
        """stop() 取消正在运行的后台 task."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        manager = AutoRotationManager(
            broker=broker,
            policy=AutoRotationPolicy(threshold_seconds=300),
            check_interval_seconds=0.01,  # 快速循环
        )
        await manager.start()
        task = manager._task
        assert task is not None and not task.done()
        await manager.stop()
        # task 应该已完成或被取消
        assert task.done() or task.cancelled()


# ── 3. AutoRotationManager sweep ─────────────────────────────────


class TestAutoRotationManagerSweep:
    """AutoRotationManager — sweep_once behavior."""

    @pytest.mark.asyncio
    async def test_sweep_once_rotates_expired_candidate(self) -> None:
        """sweep_once 在发现需轮换凭证时,撤销旧凭证并签发新凭证."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        # 签发一个即将过期的凭证
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,  # 60 秒后过期
            scopes=["chat"],
            audience="sb-1",
        )
        cred_id = cred.credential_id

        # 用极短 threshold 让它立即进入轮换窗口
        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        rotated = await manager.sweep_once()
        # 应该轮换了 1 个凭证
        assert rotated >= 1
        # 旧凭证应已被撤销
        result = broker.validate(cred_id)
        assert not result.is_valid
        assert "revoked" in result.reason

    @pytest.mark.asyncio
    async def test_sweep_once_skips_healthy_credentials(self) -> None:
        """sweep_once 不轮换仍在健康窗口的凭证."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        # 签发一个 TTL=3600 的凭证
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=3600,
        )
        cred_id = cred.credential_id

        # threshold=60 → 3600-60=3540 秒剩余,不进入窗口
        policy = AutoRotationPolicy(threshold_seconds=60)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        rotated = await manager.sweep_once()
        assert rotated == 0
        # 凭证仍然有效
        result = broker.validate(cred_id)
        assert result.is_valid

    @pytest.mark.asyncio
    async def test_sweep_once_rotates_multiple_candidates(self) -> None:
        """sweep_once 同时轮换多个候选凭证."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        # 签发 3 个即将过期的凭证
        cred_ids = []
        for i in range(3):
            cred = broker.issue(
                provider=_make_provider(),
                tenant_id="t1",
                session_id=f"s{i}",
                request_id=f"r{i}",
                ttl_seconds=60,
            )
            cred_ids.append(cred.credential_id)

        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        rotated = await manager.sweep_once()
        assert rotated == 3
        # 所有旧凭证都应被撤销
        for cid in cred_ids:
            assert not broker.validate(cid).is_valid

    @pytest.mark.asyncio
    async def test_sweep_once_no_credentials_returns_zero(self) -> None:
        """sweep_once 在 broker 无凭证时返回 0."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        policy = AutoRotationPolicy(threshold_seconds=300)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        rotated = await manager.sweep_once()
        assert rotated == 0

    @pytest.mark.asyncio
    async def test_sweep_once_skips_revoked_credentials(self) -> None:
        """sweep_once 不重复处理已撤销的凭证."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
        )
        cred_id = cred.credential_id
        # 手动撤销
        broker.revoke(cred_id)

        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        # 已撤销的不应该再被轮换
        rotated = await manager.sweep_once()
        assert rotated == 0


# ── 4. AutoRotationManager callbacks ─────────────────────────────


class TestAutoRotationManagerCallbacks:
    """AutoRotationManager — on_rotate callback firing."""

    @pytest.mark.asyncio
    async def test_on_rotate_callback_fires_with_old_and_new_ids(
        self,
    ) -> None:
        """on_rotate 回调接收 (old_id, new_cred) 元组."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
        )
        old_id = cred.credential_id

        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        events: list[tuple[str, Any]] = []
        manager.on_rotate(
            lambda old, new: events.append((old, new)),
        )
        await manager.sweep_once()

        assert len(events) == 1
        old, new = events[0]
        assert old == old_id
        # 新凭证应该是一个 ShortLivedCredential
        assert isinstance(new, ShortLivedCredential)
        # 新凭证 id 应不同于旧凭证
        assert new.credential_id != old_id

    @pytest.mark.asyncio
    async def test_multiple_callbacks_all_fire(self) -> None:
        """多个 on_rotate 回调都按注册顺序触发."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
        )

        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        order: list[str] = []
        manager.on_rotate(lambda old, new: order.append("first"))
        manager.on_rotate(lambda old, new: order.append("second"))
        manager.on_rotate(lambda old, new: order.append("third"))
        await manager.sweep_once()

        assert order == ["first", "second", "third"]


# ── 5. AutoRotationManager error handling ────────────────────────


class TestAutoRotationManagerErrorHandling:
    """AutoRotationManager — robustness against callback exceptions."""

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_break_sweep(self) -> None:
        """单个回调抛异常不影响其他回调和后续轮换."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
        )
        old_id = cred.credential_id

        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        # 第一个回调抛异常
        def bad_cb(old: str, new: Any) -> None:
            raise RuntimeError("boom")

        # 第二个回调正常
        events: list[tuple[str, Any]] = []
        manager.on_rotate(bad_cb)
        manager.on_rotate(lambda old, new: events.append((old, new)))

        # 不应抛异常
        rotated = await manager.sweep_once()
        assert rotated == 1
        # 第二个回调仍然触发
        assert len(events) == 1
        assert events[0][0] == old_id

    @pytest.mark.asyncio
    async def test_sweep_does_not_rotate_when_broker_empty(self) -> None:
        """sweep_once 在 broker cache 完全为空时不抛异常."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        policy = AutoRotationPolicy(threshold_seconds=1)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        # 不抛异常,返回 0
        result = await manager.sweep_once()
        assert result == 0


# ── 6. AutoRotationManager re-issuance failure + loop ───────────


class TestAutoRotationManagerReissueFailure:
    """AutoRotationManager — _rotate_one exception path."""

    @pytest.mark.asyncio
    async def test_rotate_one_returns_none_when_broker_issue_fails(
        self,
    ) -> None:
        """broker.issue 抛异常时,_rotate_one 返回 None,
        sweep_once 不计入 rotated,旧凭证仍被撤销."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
        )
        old_id = cred.credential_id

        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=1,
        )

        # Mock broker.issue 让它抛异常
        with patch.object(
            broker,
            "issue",
            side_effect=RuntimeError("provider down"),
        ):
            rotated = await manager.sweep_once()

        # 重新签发失败,rotated=0
        assert rotated == 0
        # 旧凭证未被撤销 (issue failed first, revoke not reached)
        result = broker.validate(old_id)
        assert result.is_valid


class TestAutoRotationManagerBackgroundLoop:
    """AutoRotationManager — _run_loop actually runs sweeps."""

    @pytest.mark.asyncio
    async def test_background_loop_sweeps_and_rotates(self) -> None:
        """start() 后后台循环自动运行 sweep_once 并轮换凭证."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        cred = broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
        )
        old_id = cred.credential_id

        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=0.05,  # 50ms 间隔
        )

        await manager.start()
        # 等待足够时间让循环至少跑一次
        await asyncio.sleep(0.2)
        await manager.stop()

        # 旧凭证应已被撤销(说明 sweep_once 被后台调用过)
        result = broker.validate(old_id)
        assert not result.is_valid

    @pytest.mark.asyncio
    async def test_background_loop_survives_sweep_exception(self) -> None:
        """sweep_once 抛异常时后台循环不退出."""
        from xruntime._runtime._credential._auto_rotation import (
            AutoRotationManager,
            AutoRotationPolicy,
        )

        broker = _make_broker()
        broker.issue(
            provider=_make_provider(),
            tenant_id="t1",
            session_id="s1",
            request_id="r1",
            ttl_seconds=60,
        )

        policy = AutoRotationPolicy(threshold_seconds=3600)
        manager = AutoRotationManager(
            broker=broker,
            policy=policy,
            check_interval_seconds=0.05,
        )

        call_count = 0

        # 替换 sweep_once 让它前两次抛异常,第三次正常
        original_sweep = manager.sweep_once

        async def flaky_sweep() -> int:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("transient")
            return await original_sweep()

        manager.sweep_once = flaky_sweep  # type: ignore[assignment]

        await manager.start()
        await asyncio.sleep(0.3)
        await manager.stop()

        # 循环至少跑了 3 次(说明异常没有杀死循环)
        assert call_count >= 3
