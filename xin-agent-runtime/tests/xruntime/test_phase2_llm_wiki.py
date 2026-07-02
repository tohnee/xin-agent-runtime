# -*- coding: utf-8 -*-
"""Tests for Phase 2: M3 LLM-Wiki MVP enhancements.

Covers:
- BM25 retrieval scoring (replaces keyword overlap)
- manifest.json index persistence
- Knowledge audit log (knowledge-audit.jsonl)
- Secret redaction before ingest
- tenant/kb physical isolation (scoped_layout)
"""
import json
import os

import pytest

from xruntime._runtime._knowledge._base import (
    KnowledgeBaseConfig,
    KnowledgeQuery,
)
from xruntime._runtime._knowledge._llm_wiki_adapter import (
    LlmWikiAdapter,
)


def _make_config(
    tmpdir: str,
    scoped: bool = False,
) -> KnowledgeBaseConfig:
    """Create a config pointing at a temp dir."""
    extra = {"scoped_layout": True} if scoped else {}
    return KnowledgeBaseConfig(
        backend="llm_wiki",
        raw_dir=os.path.join(tmpdir, "raw"),
        compiled_dir=os.path.join(tmpdir, "compiled"),
        extra=extra,
    )


async def _make_adapter(
    tmpdir: str,
    scoped: bool = False,
) -> LlmWikiAdapter:
    """Create an initialized LlmWikiAdapter in a temp dir."""
    config = _make_config(tmpdir, scoped)
    adapter = LlmWikiAdapter(config=config)
    await adapter.initialize()
    return adapter


class TestBM25Retrieval:
    """BM25 scoring replaces simple keyword overlap."""

    async def test_bm25_ranks_relevant_higher(self, tmp_path) -> None:
        """Docs with more query term occurrences score higher."""
        adapter = await _make_adapter(str(tmp_path))

        await adapter.ingest(
            source_id="doc1",
            content="Python Python Python programming language",
            title="Python",
            metadata={"tenant_id": "default", "kb_id": "default"},
        )
        await adapter.ingest(
            source_id="doc2",
            content="Python is mentioned once here",
            title="Other",
            metadata={"tenant_id": "default", "kb_id": "default"},
        )
        await adapter.compile()
        result = await adapter.retrieve(
            KnowledgeQuery(query="Python", tenant_id="default"),
        )
        assert len(result.chunks) >= 2
        assert result.chunks[0].source_id == "doc1"

    async def test_bm25_returns_score(self, tmp_path) -> None:
        """BM25 scores are positive floats."""
        adapter = await _make_adapter(str(tmp_path))

        await adapter.ingest(
            source_id="doc1",
            content="machine learning model training",
            title="ML",
            metadata={"tenant_id": "default", "kb_id": "default"},
        )
        await adapter.compile()
        result = await adapter.retrieve(
            KnowledgeQuery(query="machine learning", tenant_id="default"),
        )
        assert len(result.chunks) >= 1
        assert result.chunks[0].score > 0


class TestManifestPersistence:
    """manifest.json index is persisted to disk."""

    async def test_manifest_written_after_compile(self, tmp_path) -> None:
        """compile() writes manifest.json in the index dir."""
        adapter = await _make_adapter(str(tmp_path), scoped=True)

        await adapter.ingest(
            source_id="doc1",
            content="test content here",
            title="Test",
            metadata={"tenant_id": "acme", "kb_id": "kb1"},
        )
        await adapter.compile()

        index_dir = os.path.join(
            str(tmp_path),
            "raw",
            "tenants",
            "acme",
            "kbs",
            "kb1",
            "index",
        )
        manifest_path = os.path.join(index_dir, "manifest.json")
        assert os.path.exists(manifest_path)

        with open(manifest_path) as f:
            manifest = json.load(f)
        assert isinstance(manifest, dict)
        assert len(manifest) >= 1

    async def test_manifest_loaded_on_reinit(self, tmp_path) -> None:
        """A new adapter instance loads the existing manifest."""
        adapter1 = await _make_adapter(str(tmp_path), scoped=True)

        await adapter1.ingest(
            source_id="doc1",
            content="persisted content",
            title="Persist",
            metadata={"tenant_id": "acme", "kb_id": "kb1"},
        )
        await adapter1.compile()

        adapter2 = await _make_adapter(str(tmp_path), scoped=True)

        result = await adapter2.retrieve(
            KnowledgeQuery(
                query="persisted",
                tenant_id="acme",
                kb_ids=["kb1"],
            ),
        )
        assert len(result.chunks) >= 1


class TestKnowledgeAuditLog:
    """Knowledge operations are written to audit log."""

    async def test_ingest_writes_audit(self, tmp_path) -> None:
        """ingest() writes an entry to knowledge-audit.jsonl."""
        adapter = await _make_adapter(str(tmp_path), scoped=True)

        await adapter.ingest(
            source_id="doc1",
            content="audit test",
            title="Audit",
            metadata={"tenant_id": "acme", "kb_id": "kb1"},
        )

        audit_path = os.path.join(
            str(tmp_path),
            "raw",
            "tenants",
            "acme",
            "kbs",
            "kb1",
            "audit",
            "knowledge-audit.jsonl",
        )
        assert os.path.exists(audit_path)

        with open(audit_path) as f:
            lines = f.readlines()
        assert len(lines) >= 1
        entry = json.loads(lines[0])
        assert entry["action"] == "ingest"
        assert entry["source_id"] == "doc1"
        assert entry["tenant_id"] == "acme"

    async def test_compile_writes_audit(self, tmp_path) -> None:
        """compile() writes an audit entry."""
        adapter = await _make_adapter(str(tmp_path), scoped=True)

        await adapter.ingest(
            source_id="doc1",
            content="compile audit",
            title="Compile",
            metadata={"tenant_id": "acme", "kb_id": "kb1"},
        )
        await adapter.compile()

        audit_path = os.path.join(
            str(tmp_path),
            "raw",
            "tenants",
            "acme",
            "kbs",
            "kb1",
            "audit",
            "knowledge-audit.jsonl",
        )
        with open(audit_path) as f:
            entries = [json.loads(line) for line in f]
        actions = [e["action"] for e in entries]
        assert "compile" in actions


class TestSecretRedactionBeforeIngest:
    """Secrets are redacted before storing raw sources."""

    async def test_api_key_redacted(self, tmp_path) -> None:
        """API keys in content are redacted before ingest."""
        adapter = await _make_adapter(str(tmp_path))

        await adapter.ingest(
            source_id="secret-doc",
            content="My key is sk-abcdefghijklmnopqrstuvwxyz1234",
            title="Secrets",
            metadata={"tenant_id": "default", "kb_id": "default"},
        )

        raw_path = os.path.join(
            str(tmp_path),
            "raw",
            "secret-doc.json",
        )
        with open(raw_path) as f:
            data = json.load(f)
        assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in data["content"]
        assert "[REDACTED_API_KEY]" in data["content"]


class TestTenantKbPhysicalIsolation:
    """tenant/kb scoped_layout physically isolates files."""

    async def test_different_tenants_different_dirs(self, tmp_path) -> None:
        """Two tenants' raw files are in separate directories."""
        adapter = await _make_adapter(str(tmp_path), scoped=True)

        await adapter.ingest(
            source_id="doc-a",
            content="tenant A doc",
            title="A",
            metadata={"tenant_id": "tenant-a", "kb_id": "kb1"},
        )
        await adapter.ingest(
            source_id="doc-b",
            content="tenant B doc",
            title="B",
            metadata={"tenant_id": "tenant-b", "kb_id": "kb1"},
        )

        path_a = os.path.join(
            str(tmp_path),
            "raw",
            "tenants",
            "tenant-a",
            "kbs",
            "kb1",
            "raw",
            "doc-a.json",
        )
        path_b = os.path.join(
            str(tmp_path),
            "raw",
            "tenants",
            "tenant-b",
            "kbs",
            "kb1",
            "raw",
            "doc-b.json",
        )
        assert os.path.exists(path_a)
        assert os.path.exists(path_b)
        assert path_a != path_b

    async def test_source_id_collision_isolated(self, tmp_path) -> None:
        """Same source_id in different tenants doesn't collide."""
        adapter = await _make_adapter(str(tmp_path), scoped=True)

        await adapter.ingest(
            source_id="same-id",
            content="tenant A",
            title="A",
            metadata={"tenant_id": "ta", "kb_id": "kb1"},
        )
        await adapter.ingest(
            source_id="same-id",
            content="tenant B",
            title="B",
            metadata={"tenant_id": "tb", "kb_id": "kb1"},
        )
        await adapter.compile()

        result_a = await adapter.retrieve(
            KnowledgeQuery(
                query="tenant",
                tenant_id="ta",
                kb_ids=["kb1"],
            ),
        )
        result_b = await adapter.retrieve(
            KnowledgeQuery(
                query="tenant",
                tenant_id="tb",
                kb_ids=["kb1"],
            ),
        )
        a_contents = " ".join(c.content for c in result_a.chunks)
        b_contents = " ".join(c.content for c in result_b.chunks)
        assert "tenant A" in a_contents
        assert "tenant B" not in a_contents
        assert "tenant B" in b_contents
        assert "tenant A" not in b_contents
