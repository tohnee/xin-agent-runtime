# -*- coding: utf-8 -*-
"""Tests for XRuntime tenant RBAC policy primitives."""

import pytest

from xruntime._runtime._tenant import (
    Action,
    Principal,
    TenantRole,
    TenantMember,
    TenantPolicy,
)


class TestTenantRolePolicy:
    """WeKnora-style Owner/Admin/Contributor/Viewer matrix tests."""

    @pytest.mark.parametrize(
        "action",
        [
            Action.TENANT_READ,
            Action.TENANT_MANAGE,
            Action.TENANT_DELETE,
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
            Action.AUDIT_READ,
        ],
    )
    def test_owner_has_all_tenant_permissions(
        self,
        action: Action,
    ) -> None:
        """Owner can perform every tenant/KB governance action."""
        policy = TenantPolicy.default()
        principal = Principal(
            tenant_id="acme",
            user_id="alice",
            role=TenantRole.OWNER,
        )

        decision = policy.check(principal, action)

        assert decision.allowed is True

    def test_admin_cannot_delete_tenant(self) -> None:
        """Admin does not own the workspace and cannot delete it."""
        policy = TenantPolicy.default()
        principal = Principal(
            tenant_id="acme",
            user_id="bob",
            role=TenantRole.ADMIN,
        )

        decision = policy.check(principal, Action.TENANT_DELETE)

        assert decision.allowed is False

    def test_contributor_cannot_manage_members(self) -> None:
        """Contributor can maintain content but not membership."""
        policy = TenantPolicy.default()
        principal = Principal(
            tenant_id="acme",
            user_id="carol",
            role=TenantRole.CONTRIBUTOR,
        )

        decision = policy.check(principal, Action.MEMBER_ROLE_UPDATE)

        assert decision.allowed is False

    def test_viewer_can_query_but_cannot_ingest(self) -> None:
        """Viewer may query authorized KBs but cannot write content."""
        policy = TenantPolicy.default()
        principal = Principal(
            tenant_id="acme",
            user_id="dave",
            role=TenantRole.VIEWER,
        )

        assert policy.check(principal, Action.KB_QUERY).allowed is True
        assert policy.check(principal, Action.DOC_INGEST).allowed is False

    def test_unknown_action_defaults_to_deny(self) -> None:
        """Unknown actions are denied by default."""
        policy = TenantPolicy.default()
        principal = Principal(
            tenant_id="acme",
            user_id="erin",
            role=TenantRole.OWNER,
        )

        decision = policy.check(principal, "unknown:action")

        assert decision.allowed is False

    def test_missing_membership_defaults_to_deny(self) -> None:
        """A request without membership context is denied."""
        policy = TenantPolicy.default()
        decision = policy.check(None, Action.KB_READ)

        assert decision.allowed is False

    def test_same_user_can_have_different_roles_per_tenant(self) -> None:
        """Membership role is scoped to tenant, not global user id."""
        acme = TenantMember(
            tenant_id="acme",
            user_id="alice",
            role=TenantRole.ADMIN,
        )
        beta = TenantMember(
            tenant_id="beta",
            user_id="alice",
            role=TenantRole.VIEWER,
        )

        assert acme.role is TenantRole.ADMIN
        assert beta.role is TenantRole.VIEWER
        assert acme.tenant_id != beta.tenant_id
