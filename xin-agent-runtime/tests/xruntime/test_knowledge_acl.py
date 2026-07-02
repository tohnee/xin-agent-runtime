# -*- coding: utf-8 -*-
"""Tests for KB ACL integration and scoped LLM-Wiki layout."""

import pytest

from xruntime._runtime._knowledge._acl import (
    KnowledgeAclEntry,
    KnowledgeAclStore,
    KnowledgeBaseRecord,
)
from xruntime._runtime._knowledge._base import KnowledgeBaseConfig
from xruntime._runtime._tenant import Action, TenantRole
from xruntime._runtime._tenant._store import TenantMembershipStore

pytestmark = pytest.mark.anyio


def test_authorized_kb_ids_derived_from_membership_and_acl() -> None:
    """ACL store should derive authorized KBs from membership role."""
    members = TenantMembershipStore()
    members.upsert("acme", "viewer", TenantRole.VIEWER)
    acl = KnowledgeAclStore()
    acl.add_kb(KnowledgeBaseRecord("acme", "public", "Public"))
    acl.add_kb(KnowledgeBaseRecord("acme", "admin", "Admin"))
    acl.grant(KnowledgeAclEntry("acme", "public", TenantRole.VIEWER))
    acl.grant(KnowledgeAclEntry("acme", "admin", TenantRole.ADMIN))

    principal = members.resolve_principal("acme", "viewer")
    assert acl.get_authorized_kb_ids(principal, Action.KB_QUERY) == ["public"]


def test_viewer_cannot_ingest_knowledge() -> None:
    """Viewer has query rights but not document ingestion rights."""
    members = TenantMembershipStore()
    members.upsert("acme", "viewer", TenantRole.VIEWER)
    acl = KnowledgeAclStore()
    acl.add_kb(KnowledgeBaseRecord("acme", "public", "Public"))
    acl.grant(KnowledgeAclEntry("acme", "public", TenantRole.VIEWER))

    principal = members.resolve_principal("acme", "viewer")

    assert acl.can_access(principal, "public", Action.KB_QUERY).allowed
    assert not acl.can_access(principal, "public", Action.DOC_INGEST).allowed


def test_contributor_can_ingest_authorized_kb() -> None:
    """Contributor can ingest documents into KBs granted to contributors."""
    members = TenantMembershipStore()
    members.upsert("acme", "writer", TenantRole.CONTRIBUTOR)
    acl = KnowledgeAclStore()
    acl.add_kb(KnowledgeBaseRecord("acme", "public", "Public"))
    acl.grant(KnowledgeAclEntry("acme", "public", TenantRole.CONTRIBUTOR))

    principal = members.resolve_principal("acme", "writer")

    assert acl.can_access(principal, "public", Action.DOC_INGEST).allowed


async def test_llm_wiki_uses_tenant_kb_physical_paths(tmp_path) -> None:
    """Scoped LLM-Wiki layout should write under tenants/{tid}/kbs/{kid}."""
    from xruntime._runtime._knowledge._llm_wiki_adapter import LlmWikiAdapter

    root = tmp_path / "kb"
    adapter = LlmWikiAdapter(
        KnowledgeBaseConfig(
            raw_dir=str(root),
            compiled_dir=str(root),
            extra={"scoped_layout": True},
        ),
    )
    await adapter.initialize()
    await adapter.ingest(
        source_id="doc1",
        content="# Billing\nBilling policy.",
        title="Billing",
        metadata={"tenant_id": "acme", "kb_id": "finance"},
    )
    await adapter.compile()

    kb_root = root / "tenants" / "acme" / "kbs" / "finance"
    assert (kb_root / "raw" / "doc1.json").exists()
    # In scoped_layout mode chunk_id includes tenant/kb prefix
    assert (kb_root / "wiki" / "acme__finance__doc1__0.md").exists()
    assert (kb_root / "index" / "_index.json").exists()
    assert (kb_root / "index" / "manifest.json").exists()


async def test_static_knowledge_injection_does_not_leak_restricted_kb() -> None:
    """Extension should derive authorized KB ids from ACL for injection."""
    from xruntime._config import XRuntimeConfig
    from xruntime._gateway._extension import create_xruntime_extension
    from xruntime._infra._tenant import current_tenant
    from xruntime._runtime._knowledge._middleware import KnowledgeMiddleware

    members = TenantMembershipStore()
    members.upsert("acme", "viewer", TenantRole.VIEWER)
    acl = KnowledgeAclStore()
    acl.add_kb(KnowledgeBaseRecord("acme", "public", "Public"))
    acl.add_kb(KnowledgeBaseRecord("acme", "restricted", "Restricted"))
    acl.grant(KnowledgeAclEntry("acme", "public", TenantRole.VIEWER))
    acl.grant(KnowledgeAclEntry("acme", "restricted", TenantRole.ADMIN))

    # Override the conftest's ``current_tenant`` seed (``"test-tenant"``)
    # with ``"acme"`` so the middleware_factory's effective_tenant matches
    # the membership store / ACL store tenant id. The conftest fixture
    # clears ``current_tenant`` after the test, so this override does not
    # leak to other tests.
    current_tenant.set("acme")

    cfg = XRuntimeConfig.model_validate(
        {
            "knowledge": {
                "enabled": True,
                "raw_dir": "/tmp/xruntime-test-raw",
                "compiled_dir": "/tmp/xruntime-test-compiled",
            },
        },
    )
    ext = create_xruntime_extension(
        config=cfg,
        tenant_id="acme",
        membership_store=members,
        knowledge_acl_store=acl,
    )
    middlewares = await ext["extra_agent_middlewares"](
        "viewer",
        "agent-1",
        "sess-1",
    )
    knowledge = next(
        mw for mw in middlewares if isinstance(mw, KnowledgeMiddleware)
    )

    assert knowledge.kb_ids == ["public"]
