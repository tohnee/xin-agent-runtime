# -*- coding: utf-8 -*-
"""Knowledge-base ownership and ACL primitives."""
from __future__ import annotations

from dataclasses import dataclass, field

from .._tenant import Action, PolicyDecision, TenantPolicy, TenantRole
from .._tenant._store import AuthPrincipal


@dataclass(frozen=True)
class KnowledgeBaseRecord:
    """A tenant-scoped knowledge base."""

    tenant_id: str
    kb_id: str
    name: str
    owner_user_id: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeAclEntry:
    """Role grant for a knowledge base."""

    tenant_id: str
    kb_id: str
    role: TenantRole


class KnowledgeAclStore:
    """In-memory KB ACL store."""

    def __init__(self, policy: TenantPolicy | None = None) -> None:
        """Initialize the store."""
        self._policy = policy or TenantPolicy.default()
        self._kbs: dict[tuple[str, str], KnowledgeBaseRecord] = {}
        self._grants: set[tuple[str, str, TenantRole]] = set()

    def add_kb(self, record: KnowledgeBaseRecord) -> None:
        """Register a tenant-scoped knowledge base."""
        self._kbs[(record.tenant_id, record.kb_id)] = record

    def grant(self, entry: KnowledgeAclEntry) -> None:
        """Grant a role access to a KB."""
        self._grants.add((entry.tenant_id, entry.kb_id, entry.role))

    def can_access(
        self,
        principal: AuthPrincipal | None,
        kb_id: str,
        action: Action,
    ) -> PolicyDecision:
        """Return whether a principal can access a KB for an action."""
        decision = self._policy.check(principal, action)
        if not decision.allowed or principal is None:
            return decision
        record = self._kbs.get((principal.tenant_id, kb_id))
        if record is None:
            return PolicyDecision(False, "knowledge base not found")
        if record.owner_user_id and record.owner_user_id == principal.user_id:
            return PolicyDecision(True, "allowed by KB ownership")
        if (principal.tenant_id, kb_id, principal.role) in self._grants:
            return PolicyDecision(True, "allowed by KB ACL")
        return PolicyDecision(False, "KB ACL denied")

    def get_authorized_kb_ids(
        self,
        principal: AuthPrincipal | None,
        action: Action,
    ) -> list[str]:
        """List KB ids visible to a principal for an action."""
        if principal is None:
            return []
        authorized: list[str] = []
        for (tenant_id, kb_id), _record in sorted(self._kbs.items()):
            if tenant_id != principal.tenant_id:
                continue
            if self.can_access(principal, kb_id, action).allowed:
                authorized.append(kb_id)
        return authorized
