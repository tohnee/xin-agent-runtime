# -*- coding: utf-8 -*-
"""Regression tests for MiddlewareStateCache concurrency safety.

These tests guard against the previous bug where
``get_knowledge_middleware`` mutated a shared singleton's
``user_id``/``kb_ids``/``role`` fields, causing cross-tenant data
leakage under concurrent requests.
"""
from __future__ import annotations

import asyncio

import pytest

from xruntime._config import XRuntimeConfig
from xruntime._gateway._mw_state import MiddlewareStateCache


def _make_cache(enabled: bool = True) -> MiddlewareStateCache:
    import tempfile

    tmp = tempfile.mkdtemp(prefix="xruntime_mw_test_")
    config = XRuntimeConfig()
    config.knowledge.enabled = enabled
    config.knowledge.raw_dir = f"{tmp}/raw"
    config.knowledge.compiled_dir = f"{tmp}/compiled"
    config.observability.audit_storage = "memory"
    config.observability.audit_enabled = True
    return MiddlewareStateCache(config=config, tenant_id="default")


class TestKnowledgeMiddlewarePerCallIsolation:
    """Each call to ``get_knowledge_middleware`` must return an
    instance whose per-call fields (user_id/kb_ids/role/tenant_id)
    reflect the arguments of *that* call, never the arguments of a
    concurrent or previous call.
    """

    @pytest.mark.asyncio
    async def test_sequential_calls_get_distinct_instances(
        self,
    ) -> None:
        """Two sequential calls with different user_id must not
        share mutable state — either via distinct instances or via
        a snapshot pattern."""
        cache = _make_cache()
        mw_a = await cache.get_knowledge_middleware(
            user_id="alice",
            kb_ids=["kb-a"],
            role="viewer",
        )
        mw_b = await cache.get_knowledge_middleware(
            user_id="bob",
            kb_ids=["kb-b"],
            role="admin",
        )
        assert mw_a is not None
        assert mw_b is not None
        assert mw_a.user_id == "alice"
        assert mw_a.kb_ids == ["kb-a"]
        assert mw_a.role == "viewer"
        assert mw_b.user_id == "bob"
        assert mw_b.kb_ids == ["kb-b"]
        assert mw_b.role == "admin"

    @pytest.mark.asyncio
    async def test_concurrent_calls_do_not_crossover(self) -> None:
        """Concurrent calls must not observe each other's fields.

        This is the regression test for the singleton-mutation bug:
        previously both calls wrote to the same shared instance, and
        the last writer won, leaking cross-tenant user/kb scope.
        """
        cache = _make_cache()

        barrier = asyncio.Event()

        async def hold_then_read(
            user_id: str,
            kb_ids: list[str],
            role: str,
        ):
            mw = await cache.get_knowledge_middleware(
                user_id=user_id,
                kb_ids=kb_ids,
                role=role,
            )
            # Hold the reference; let the other coroutine run.
            barrier.set()
            # Yield to allow the other call to mutate (if bug present).
            for _ in range(5):
                await asyncio.sleep(0)
            return mw

        mw_alice, mw_bob = await asyncio.gather(
            hold_then_read("alice", ["kb-a"], "viewer"),
            hold_then_read("bob", ["kb-b"], "admin"),
        )

        assert mw_alice.user_id == "alice", (
            "alice's middleware observed bob's user_id — "
            "concurrent mutation leaked cross-tenant scope"
        )
        assert mw_alice.kb_ids == ["kb-a"]
        assert mw_alice.role == "viewer"
        assert mw_bob.user_id == "bob"
        assert mw_bob.kb_ids == ["kb-b"]
        assert mw_bob.role == "admin"
