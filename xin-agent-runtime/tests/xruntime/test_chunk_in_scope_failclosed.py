# -*- coding: utf-8 -*-
"""Tests for ``_chunk_in_scope`` fail-closed ``kb_id`` behavior.

The scope check must be fail-closed for both ``tenant_id`` and
``kb_id``: chunks missing either field are rejected to prevent
cross-tenant / cross-KB data leakage.
"""

from xruntime._runtime._knowledge._base import (
    KnowledgeChunk,
    KnowledgeQuery,
)
from xruntime._runtime._knowledge._registry import _chunk_in_scope


def _make_chunk(metadata: dict) -> KnowledgeChunk:
    """Build a minimal chunk with the given metadata."""
    return KnowledgeChunk(
        chunk_id="c1",
        source_id="s1",
        title="T",
        content="C",
        score=0.5,
        metadata=metadata,
    )


def _make_query(tenant_id: str = "acme", kb_ids=None) -> KnowledgeQuery:
    """Build a query with the given tenant and kb scope."""
    return KnowledgeQuery(
        query="q",
        tenant_id=tenant_id,
        kb_ids=kb_ids if kb_ids is not None else [],
    )


# ---------------------------------------------------------------------------
# Fail-closed: chunk missing kb_id key is rejected when query specifies kb_ids
# ---------------------------------------------------------------------------


def test_chunk_missing_kb_id_key_rejected_when_kb_ids_specified() -> None:
    """Chunk without ``kb_id`` metadata must be rejected when query scopes by kb."""
    chunk = _make_chunk({"tenant_id": "acme"})
    query = _make_query(tenant_id="acme", kb_ids=["kb-1"])
    assert _chunk_in_scope(chunk, query) is False


def test_chunk_missing_kb_id_key_rejected_even_when_default_in_kb_ids() -> (
    None
):
    """Chunk missing ``kb_id`` must be rejected even if ``"default"`` is authorized.

    This is the core fail-closed test: the legacy code defaulted a missing
    ``kb_id`` to ``"default"`` which could match an authorized ``"default"``
    KB and leak unscoped chunks. The fix rejects chunks with no ``kb_id``
    regardless of the query's authorized list.
    """
    chunk = _make_chunk({"tenant_id": "acme"})
    query = _make_query(tenant_id="acme", kb_ids=["default"])
    assert _chunk_in_scope(chunk, query) is False


# ---------------------------------------------------------------------------
# Fail-closed: kb_id is None or empty string is rejected
# ---------------------------------------------------------------------------


def test_chunk_kb_id_none_rejected() -> None:
    """Chunk with ``kb_id=None`` must be rejected."""
    chunk = _make_chunk({"tenant_id": "acme", "kb_id": None})
    query = _make_query(tenant_id="acme", kb_ids=["kb-1"])
    assert _chunk_in_scope(chunk, query) is False


def test_chunk_kb_id_none_rejected_even_when_default_in_kb_ids() -> None:
    """Chunk with ``kb_id=None`` must be rejected even if ``"default"`` is authorized."""
    chunk = _make_chunk({"tenant_id": "acme", "kb_id": None})
    query = _make_query(tenant_id="acme", kb_ids=["default"])
    assert _chunk_in_scope(chunk, query) is False


def test_chunk_kb_id_empty_string_rejected() -> None:
    """Chunk with ``kb_id=""`` must be rejected."""
    chunk = _make_chunk({"tenant_id": "acme", "kb_id": ""})
    query = _make_query(tenant_id="acme", kb_ids=["kb-1"])
    assert _chunk_in_scope(chunk, query) is False


def test_chunk_kb_id_empty_string_rejected_even_when_default_in_kb_ids() -> (
    None
):
    """Chunk with ``kb_id=""`` must be rejected even if ``"default"`` is authorized."""
    chunk = _make_chunk({"tenant_id": "acme", "kb_id": ""})
    query = _make_query(tenant_id="acme", kb_ids=["default"])
    assert _chunk_in_scope(chunk, query) is False


# ---------------------------------------------------------------------------
# Positive case: matching kb_id passes
# ---------------------------------------------------------------------------


def test_chunk_kb_id_matching_passes() -> None:
    """Chunk with a matching ``kb_id`` passes the scope check."""
    chunk = _make_chunk({"tenant_id": "acme", "kb_id": "kb-1"})
    query = _make_query(tenant_id="acme", kb_ids=["kb-1"])
    assert _chunk_in_scope(chunk, query) is True


def test_chunk_with_explicit_default_kb_id_passes_default_query() -> None:
    """Chunk explicitly tagged ``kb_id="default"`` passes when ``"default"`` is authorized.

    This verifies the fix doesn't break legitimate ``"default"`` KB usage —
    only *missing* / None / empty ``kb_id`` is rejected.
    """
    chunk = _make_chunk({"tenant_id": "acme", "kb_id": "default"})
    query = _make_query(tenant_id="acme", kb_ids=["default"])
    assert _chunk_in_scope(chunk, query) is True


# ---------------------------------------------------------------------------
# Backward compat: empty kb_ids skips kb check (tenant-only filter)
# ---------------------------------------------------------------------------


def test_empty_kb_ids_skips_kb_check() -> None:
    """Empty ``kb_ids`` means all KBs in the tenant are eligible (no kb_id check)."""
    chunk = _make_chunk({"tenant_id": "acme"})  # no kb_id
    query = _make_query(tenant_id="acme", kb_ids=[])
    assert _chunk_in_scope(chunk, query) is True


# ---------------------------------------------------------------------------
# No regression: tenant_id fail-closed preserved
# ---------------------------------------------------------------------------


def test_tenant_id_missing_rejected() -> None:
    """Chunk without ``tenant_id`` must be rejected (fail-closed, no regression)."""
    chunk = _make_chunk({})
    query = _make_query(tenant_id="acme", kb_ids=[])
    assert _chunk_in_scope(chunk, query) is False


def test_tenant_id_none_rejected() -> None:
    """Chunk with ``tenant_id=None`` must be rejected (fail-closed, no regression)."""
    chunk = _make_chunk({"tenant_id": None})
    query = _make_query(tenant_id="acme", kb_ids=[])
    assert _chunk_in_scope(chunk, query) is False


def test_tenant_id_empty_string_rejected() -> None:
    """Chunk with ``tenant_id=""`` must be rejected (fail-closed, no regression)."""
    chunk = _make_chunk({"tenant_id": ""})
    query = _make_query(tenant_id="acme", kb_ids=[])
    assert _chunk_in_scope(chunk, query) is False


# ---------------------------------------------------------------------------
# No regression: tenant matches but kb_id mismatch is rejected
# ---------------------------------------------------------------------------


def test_tenant_matches_but_kb_id_mismatch_rejected() -> None:
    """Chunk whose tenant matches but kb_id doesn't must be rejected."""
    chunk = _make_chunk({"tenant_id": "acme", "kb_id": "kb-1"})
    query = _make_query(tenant_id="acme", kb_ids=["kb-2"])
    assert _chunk_in_scope(chunk, query) is False


def test_tenant_mismatch_rejected_regardless_of_kb_id() -> None:
    """Chunk whose tenant doesn't match is rejected regardless of kb_id."""
    chunk = _make_chunk({"tenant_id": "other", "kb_id": "kb-1"})
    query = _make_query(tenant_id="acme", kb_ids=["kb-1"])
    assert _chunk_in_scope(chunk, query) is False
