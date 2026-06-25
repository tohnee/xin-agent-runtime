# -*- coding: utf-8 -*-
"""WeKnora-style tenant RBAC policy model.

The policy is intentionally independent from AgentScope's tool-level
permission engine.  It answers enterprise governance questions such as
whether a tenant member may manage members, query a knowledge base, or
ingest documents.  Tool middleware can then translate tool names into
these higher-level actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TenantRole(StrEnum):
    """Tenant-scoped roles ordered from broadest to narrowest."""

    OWNER = "owner"
    ADMIN = "admin"
    CONTRIBUTOR = "contributor"
    VIEWER = "viewer"


class Action(StrEnum):
    """Fine-grained tenant, KB, document, tool, and audit actions."""

    TENANT_READ = "tenant:read"
    TENANT_MANAGE = "tenant:manage"
    TENANT_DELETE = "tenant:delete"
    MEMBER_INVITE = "member:invite"
    MEMBER_ROLE_UPDATE = "member:role_update"
    KB_CREATE = "kb:create"
    KB_READ = "kb:read"
    KB_QUERY = "kb:query"
    KB_UPDATE = "kb:update"
    KB_DELETE = "kb:delete"
    DOC_INGEST = "doc:ingest"
    DOC_UPDATE = "doc:update"
    DOC_DELETE = "doc:delete"
    TOOL_EXECUTE = "tool:execute"
    MODEL_USE = "model:use"
    AUDIT_READ = "audit:read"


@dataclass(frozen=True)
class TenantMember:
    """A user's membership in one tenant/workspace.

    Args:
        tenant_id (`str`):
            Tenant/workspace identifier.
        user_id (`str`):
            User identifier.
        role (`TenantRole`):
            Role assigned within this tenant.
        status (`str`):
            Membership status. Only ``"active"`` members are allowed.
    """

    tenant_id: str
    user_id: str
    role: TenantRole
    status: str = "active"
    invited_by: str | None = None


@dataclass(frozen=True)
class Principal:
    """Authenticated request principal bound to one tenant."""

    tenant_id: str
    user_id: str
    role: TenantRole

    @classmethod
    def from_member(cls, member: TenantMember) -> "Principal | None":
        """Create a principal from an active membership.

        Args:
            member (`TenantMember`): Membership record.

        Returns:
            `Principal | None`: The principal, or ``None`` if inactive.
        """
        if member.status != "active":
            return None
        return cls(
            tenant_id=member.tenant_id,
            user_id=member.user_id,
            role=member.role,
        )


@dataclass(frozen=True)
class PolicyDecision:
    """Result of a policy check."""

    allowed: bool
    reason: str


class TenantPolicy:
    """Static role/action matrix for tenant governance.

    The default matrix follows the enterprise design in
    ``docs/xruntime/ENTERPRISE-RUNTIME-ROADMAP.md``: Owner has full
    governance rights, Admin can manage most tenant and KB resources but
    cannot delete the tenant, Contributor can maintain KB content, and
    Viewer can only read/query.
    """

    def __init__(
        self,
        matrix: dict[TenantRole, set[Action]],
    ) -> None:
        """Initialize the policy matrix."""
        self._matrix = matrix

    @classmethod
    def default(cls) -> "TenantPolicy":
        """Return the default Owner/Admin/Contributor/Viewer matrix."""
        owner = set(Action)
        admin = {
            Action.TENANT_READ,
            Action.TENANT_MANAGE,
            Action.MEMBER_INVITE,
            Action.MEMBER_ROLE_UPDATE,
            Action.KB_CREATE,
            Action.KB_READ,
            Action.KB_QUERY,
            Action.KB_UPDATE,
            Action.KB_DELETE,
            Action.DOC_INGEST,
            Action.DOC_UPDATE,
            Action.DOC_DELETE,
            Action.TOOL_EXECUTE,
            Action.MODEL_USE,
            Action.AUDIT_READ,
        }
        contributor = {
            Action.TENANT_READ,
            Action.KB_READ,
            Action.KB_QUERY,
            Action.KB_UPDATE,
            Action.DOC_INGEST,
            Action.DOC_UPDATE,
            Action.TOOL_EXECUTE,
            Action.MODEL_USE,
        }
        viewer = {
            Action.TENANT_READ,
            Action.KB_READ,
            Action.KB_QUERY,
            Action.MODEL_USE,
        }
        return cls(
            {
                TenantRole.OWNER: owner,
                TenantRole.ADMIN: admin,
                TenantRole.CONTRIBUTOR: contributor,
                TenantRole.VIEWER: viewer,
            }
        )

    def check(
        self,
        principal: Principal | None,
        action: Action | str,
        resource: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Check whether a principal can perform an action.

        Args:
            principal (`Principal | None`): Authenticated principal.
            action (`Action | str`): Requested action.
            resource (`dict | None`): Optional resource metadata reserved
                for per-KB ownership and ACL checks.

        Returns:
            `PolicyDecision`: Allow/deny decision with a reason.
        """
        del resource
        if principal is None:
            return PolicyDecision(False, "missing tenant membership")
        try:
            normalized_action = Action(action)
        except ValueError:
            return PolicyDecision(False, f"unknown action: {action}")
        allowed = normalized_action in self._matrix.get(principal.role, set())
        if allowed:
            return PolicyDecision(True, "allowed by tenant role")
        return PolicyDecision(
            False,
            f"role {principal.role.value} cannot {normalized_action.value}",
        )
