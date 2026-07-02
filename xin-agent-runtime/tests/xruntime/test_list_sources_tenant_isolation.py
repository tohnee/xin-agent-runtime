# -*- coding: utf-8 -*-
"""Regression tests for ``LlmWikiAdapter.list_sources`` tenant
isolation.

These tests guard against two previous bugs:

1. Field path mismatch — ``list_sources`` read ``tenant_id`` from the
   JSON top level, but :meth:`ingest` nests it under ``metadata``.
   The filter therefore always saw ``"default"`` and either returned
   every tenant's sources (when the caller asked for ``"default"``)
   or none at all.
2. Scoped-layout directory traversal — when ``scoped_layout`` is
   enabled, sources live under
   ``raw/tenants/<tid>/kbs/<kid>/raw/*.json`` but ``list_sources``
   only iterated ``raw_dir`` top level.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from xruntime._runtime._knowledge._base import KnowledgeBaseConfig
from xruntime._runtime._knowledge._llm_wiki_adapter import LlmWikiAdapter


def _config(tmpdir: str, scoped: bool = False) -> KnowledgeBaseConfig:
    extra = {"scoped_layout": True} if scoped else {}
    return KnowledgeBaseConfig(
        backend="llm_wiki",
        raw_dir=os.path.join(tmpdir, "raw"),
        compiled_dir=os.path.join(tmpdir, "compiled"),
        auto_compile=False,
        extra=extra,
    )


class TestListSourcesTenantIsolationFlat:
    """Flat layout (no ``scoped_layout``)."""

    @pytest.mark.asyncio
    async def test_default_tenant_does_not_leak_other_tenants(
        self,
    ) -> None:
        """list_sources("default") must NOT return sources whose
        metadata.tenant_id != "default"."""
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = LlmWikiAdapter(_config(tmpdir))
            await adapter.initialize()

            await adapter.ingest(
                source_id="alice_doc",
                content="alice's secret",
                title="Alice Doc",
                metadata={"tenant_id": "t_alice"},
            )
            await adapter.ingest(
                source_id="default_doc",
                content="public doc",
                title="Default Doc",
                metadata={"tenant_id": "default"},
            )
            await adapter.ingest(
                source_id="bob_doc",
                content="bob's secret",
                title="Bob Doc",
                metadata={"tenant_id": "t_bob"},
            )

            default_sources = await adapter.list_sources(
                tenant_id="default",
            )
            ids = {s.source_id for s in default_sources}
            assert ids == {"default_doc"}, (
                f"expected only default_doc, got {ids} — "
                "list_sources leaked cross-tenant sources"
            )

            alice_sources = await adapter.list_sources(
                tenant_id="t_alice",
            )
            assert {s.source_id for s in alice_sources} == {"alice_doc"}

            bob_sources = await adapter.list_sources(
                tenant_id="t_bob",
            )
            assert {s.source_id for s in bob_sources} == {"bob_doc"}

    @pytest.mark.asyncio
    async def test_unknown_tenant_returns_empty(self) -> None:
        """An unknown tenant should see no sources."""
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = LlmWikiAdapter(_config(tmpdir))
            await adapter.initialize()
            await adapter.ingest(
                source_id="doc1",
                content="x",
                title="t",
                metadata={"tenant_id": "t_known"},
            )
            result = await adapter.list_sources(tenant_id="t_unknown")
            assert result == []


class TestListSourcesTenantIsolationScoped:
    """Scoped layout (``scoped_layout: True``).

    Sources are physically stored under
    ``raw/tenants/<tid>/kbs/<kid>/raw/*.json``. ``list_sources`` must
    walk these subdirectories and still apply tenant filtering.
    """

    @pytest.mark.asyncio
    async def test_scoped_layout_walks_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = LlmWikiAdapter(_config(tmpdir, scoped=True))
            await adapter.initialize()

            await adapter.ingest(
                source_id="alice_kb1_doc",
                content="alice kb1",
                title="Alice KB1",
                metadata={
                    "tenant_id": "t_alice",
                    "kb_id": "kb1",
                },
            )
            await adapter.ingest(
                source_id="alice_kb2_doc",
                content="alice kb2",
                title="Alice KB2",
                metadata={
                    "tenant_id": "t_alice",
                    "kb_id": "kb2",
                },
            )
            await adapter.ingest(
                source_id="bob_doc",
                content="bob",
                title="Bob",
                metadata={
                    "tenant_id": "t_bob",
                    "kb_id": "kb1",
                },
            )

            alice_sources = await adapter.list_sources(
                tenant_id="t_alice",
            )
            assert {s.source_id for s in alice_sources} == {
                "alice_kb1_doc",
                "alice_kb2_doc",
            }

            bob_sources = await adapter.list_sources(
                tenant_id="t_bob",
            )
            assert {s.source_id for s in bob_sources} == {"bob_doc"}
