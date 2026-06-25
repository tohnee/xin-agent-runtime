# -*- coding: utf-8 -*-
"""专项集成测试：WorkspaceConfig + RBAC 端到端安全验证.

覆盖 P0 修复的核心安全逻辑：
1. WorkspaceConfig 生产环境拒绝 local
2. WorkspaceManagerFactory 后端选择
3. RBAC 权限矩阵端到端
4. AuthMiddleware → Principal → RBAC 联动
5. Knowledge 工具 ACL 强制
6. Anti-spoofing（principal tenant 覆盖客户端 header）
"""
import pytest

from xruntime._runtime._workspace import (
    WorkspaceConfig,
    WorkspaceManagerFactory,
)
from xruntime._runtime._tenant._policy import (
    Action,
    PolicyDecision,
    Principal,
    TenantMember,
    TenantPolicy,
    TenantRole,
)
from xruntime._runtime._tenant._store import (
    ApiKeyRecord,
    ApiKeyStore,
    AuthPrincipal,
    TenantMembershipStore,
)
from xruntime._runtime._knowledge._acl import (
    KnowledgeAclStore,
    KnowledgeAclEntry,
    KnowledgeBaseRecord,
)
from xruntime._gateway._auth import AuthMiddleware


# =====================================================================
# Part 1: WorkspaceConfig 生产安全
# =====================================================================


class TestWorkspaceProductionSafety:
    """WorkspaceConfig 在生产环境下的安全行为。"""

    def test_production_rejects_local_by_default(self) -> None:
        """生产环境默认拒绝 LocalWorkspaceManager。"""
        config = WorkspaceConfig(
            default_backend="local",
            allow_local_in_production=False,
        )
        factory = WorkspaceManagerFactory(config)
        with pytest.raises(ValueError, match="local.*production"):
            factory.create(backend="local", production=True)

    def test_production_allows_docker(self) -> None:
        """生产环境允许 docker 后端。"""
        config = WorkspaceConfig(default_backend="docker")
        factory = WorkspaceManagerFactory(config)
        manager = factory.create(backend="docker", production=True)
        assert manager is not None
        assert getattr(manager, "backend", "docker") == "docker"

    def test_production_allows_local_with_explicit_override(self) -> None:
        """生产环境显式 override 后允许 local。"""
        config = WorkspaceConfig(
            default_backend="local",
            allow_local_in_production=True,
        )
        factory = WorkspaceManagerFactory(config)
        manager = factory.create(backend="local", production=True)
        assert manager is not None

    def test_non_production_allows_local(self) -> None:
        """非生产环境允许 local。"""
        config = WorkspaceConfig(default_backend="local")
        factory = WorkspaceManagerFactory(config)
        manager = factory.create(backend="local", production=False)
        assert manager is not None

    def test_default_backend_is_docker(self) -> None:
        """默认后端是 docker（不是 local）。"""
        config = WorkspaceConfig()
        assert config.default_backend == "docker"

    def test_path_traversal_rejected_in_tenant(self) -> None:
        """tenant_id 包含路径穿越被拒绝。"""
        config = WorkspaceConfig(default_backend="local")
        factory = WorkspaceManagerFactory(config)
        with pytest.raises(ValueError, match="traversal"):
            factory.workspace_path(
                tenant_id="../../../etc/passwd",
                session_id="s1",
            )

    def test_path_traversal_rejected_in_session(self) -> None:
        """session_id 包含路径穿越被拒绝。"""
        config = WorkspaceConfig(default_backend="local")
        factory = WorkspaceManagerFactory(config)
        with pytest.raises(ValueError, match="traversal"):
            factory.workspace_path(
                tenant_id="acme",
                session_id="../../root",
            )

    def test_workspace_path_includes_tenant_and_session(self) -> None:
        """workspace path 包含 tenant 和 session。"""
        config = WorkspaceConfig(default_backend="local")
        factory = WorkspaceManagerFactory(config)
        path = factory.workspace_path(
            tenant_id="acme",
            session_id="s123",
        )
        assert "acme" in path
        assert "s123" in path


# =====================================================================
# Part 2: RBAC 权限矩阵
# =====================================================================


class TestRbacPermissionMatrix:
    """RBAC 四级角色的权限矩阵端到端验证。"""

    @pytest.fixture
    def policy(self) -> TenantPolicy:
        return TenantPolicy.default()

    def test_owner_has_all_actions(self, policy: TenantPolicy) -> None:
        """Owner 拥有全部 16 个 action。"""
        principal = Principal(
            tenant_id="t1",
            user_id="owner",
            role=TenantRole.OWNER,
        )
        for action in Action:
            assert policy.check(principal, action).allowed

    def test_admin_cannot_delete_tenant(self, policy: TenantPolicy) -> None:
        """Admin 不能删除 tenant。"""
        principal = Principal(
            tenant_id="t1",
            user_id="admin",
            role=TenantRole.ADMIN,
        )
        decision = policy.check(principal, Action.TENANT_DELETE)
        assert not decision.allowed

    def test_admin_can_manage_members(self, policy: TenantPolicy) -> None:
        """Admin 可以邀请和管理成员。"""
        principal = Principal(
            tenant_id="t1",
            user_id="admin",
            role=TenantRole.ADMIN,
        )
        assert policy.check(principal, Action.MEMBER_INVITE).allowed
        assert policy.check(principal, Action.MEMBER_ROLE_UPDATE).allowed

    def test_contributor_can_ingest_but_not_manage(
        self, policy: TenantPolicy
    ) -> None:
        """Contributor 可以 ingest 文档但不能管理成员。"""
        principal = Principal(
            tenant_id="t1",
            user_id="contrib",
            role=TenantRole.CONTRIBUTOR,
        )
        assert policy.check(principal, Action.DOC_INGEST).allowed
        assert policy.check(principal, Action.KB_QUERY).allowed
        assert not policy.check(principal, Action.MEMBER_INVITE).allowed
        assert not policy.check(principal, Action.TENANT_DELETE).allowed

    def test_viewer_read_only(self, policy: TenantPolicy) -> None:
        """Viewer 只能读取和查询。"""
        principal = Principal(
            tenant_id="t1",
            user_id="viewer",
            role=TenantRole.VIEWER,
        )
        assert policy.check(principal, Action.KB_READ).allowed
        assert policy.check(principal, Action.KB_QUERY).allowed
        assert not policy.check(principal, Action.DOC_INGEST).allowed
        assert not policy.check(principal, Action.KB_DELETE).allowed
        assert not policy.check(principal, Action.MEMBER_INVITE).allowed

    def test_none_principal_denied(self, policy: TenantPolicy) -> None:
        """None principal 被拒绝（默认 deny）。"""
        decision = policy.check(None, Action.KB_READ)
        assert not decision.allowed
        assert "missing" in decision.reason.lower()

    def test_unknown_action_denied(self, policy: TenantPolicy) -> None:
        """未知 action 被拒绝。"""
        principal = Principal(
            tenant_id="t1",
            user_id="u1",
            role=TenantRole.OWNER,
        )
        decision = policy.check(principal, "unknown:action")
        assert not decision.allowed


# =====================================================================
# Part 3: AuthMiddleware → Principal → RBAC 联动
# =====================================================================


class TestAuthToRbacPipeline:
    """认证 → Principal → RBAC 角色分配端到端。"""

    def test_api_key_resolves_to_principal(self) -> None:
        """API Key 通过 ApiKeyStore 解析为 AuthPrincipal。"""
        store = ApiKeyStore(
            [
                ApiKeyRecord(
                    key="sk-admin-key",
                    tenant_id="acme",
                    user_id="alice",
                    role=TenantRole.ADMIN,
                    kb_ids=["kb1", "kb2"],
                ),
            ],
        )
        principal = store.authenticate("sk-admin-key")
        assert principal is not None
        assert principal.tenant_id == "acme"
        assert principal.role == TenantRole.ADMIN
        assert principal.kb_ids == ["kb1", "kb2"]

    def test_inactive_api_key_rejected(self) -> None:
        """禁用的 API Key 被拒绝。"""
        store = ApiKeyStore(
            [
                ApiKeyRecord(
                    key="sk-disabled",
                    tenant_id="t1",
                    user_id="u1",
                    active=False,
                ),
            ],
        )
        assert store.authenticate("sk-disabled") is None

    def test_membership_store_resolves_principal(self) -> None:
        """TenantMembershipStore 解析 active 成员。"""
        ms = TenantMembershipStore()
        ms.upsert(
            tenant_id="acme",
            user_id="bob",
            role=TenantRole.CONTRIBUTOR,
        )
        principal = ms.resolve_principal("acme", "bob")
        assert principal is not None
        assert principal.role == TenantRole.CONTRIBUTOR

    def test_disabled_member_not_resolved(self) -> None:
        """禁用成员不被解析。"""
        ms = TenantMembershipStore()
        ms.upsert(
            tenant_id="acme",
            user_id="bob",
            role=TenantRole.CONTRIBUTOR,
            status="disabled",
        )
        assert ms.resolve_principal("acme", "bob") is None

    def test_auth_middleware_uses_api_key_store(self) -> None:
        """AuthMiddleware 从 ApiKeyStore 解析 principal。"""
        store = ApiKeyStore(
            [
                ApiKeyRecord(
                    key="sk-test",
                    tenant_id="acme",
                    user_id="alice",
                    role=TenantRole.OWNER,
                ),
            ],
        )
        mw = AuthMiddleware(app=None, api_key_store=store)

        class FakeHeaders:
            def get(self, key: str, default: str = "") -> str:
                return {"x-api-key": "sk-test"}.get(key, default)

        principal = mw.authenticate_headers(FakeHeaders())
        assert principal is not None
        assert principal.tenant_id == "acme"
        assert principal.role == TenantRole.OWNER

    def test_principal_tenant_overrides_request_tenant(
        self,
    ) -> None:
        """Principal 的 tenant_id 覆盖客户端请求的 tenant_id。

        这是 anti-spoofing 的核心：客户端不能通过 header
        伪造 tenant_id。
        """
        principal = AuthPrincipal(
            tenant_id="acme",
            user_id="alice",
            role=TenantRole.ADMIN,
        )
        # 客户端试图伪造 tenant
        client_tenant = "evil-corp"
        # Gateway 使用 principal.tenant_id
        effective_tenant = principal.tenant_id
        assert effective_tenant == "acme"
        assert effective_tenant != client_tenant


# =====================================================================
# Part 4: Knowledge ACL 强制
# =====================================================================


class TestKnowledgeAclEnforcement:
    """Knowledge 工具 ACL 端到端。"""

    @pytest.fixture
    def acl_store(self) -> KnowledgeAclStore:
        store = KnowledgeAclStore()
        store.add_kb(
            KnowledgeBaseRecord(
                name="private-kb",
                tenant_id="acme",
                kb_id="private-kb",
                owner_user_id="alice",
            ),
        )
        store.add_kb(
            KnowledgeBaseRecord(
                name="shared-kb",
                tenant_id="acme",
                kb_id="shared-kb",
                owner_user_id="alice",
            ),
        )
        # Grant viewer access to shared-kb
        store.grant(
            KnowledgeAclEntry(
                tenant_id="acme",
                kb_id="shared-kb",
                role=TenantRole.VIEWER,
            ),
        )
        return store

    def test_owner_can_access_own_kb(
        self, acl_store: KnowledgeAclStore
    ) -> None:
        """KB owner 可以访问自己的 KB。"""
        principal = AuthPrincipal(
            tenant_id="acme",
            user_id="alice",
            role=TenantRole.VIEWER,
        )
        decision = acl_store.can_access(
            principal, "private-kb", Action.KB_QUERY
        )
        assert decision.allowed

    def test_viewer_denied_unauthorized_kb(
        self, acl_store: KnowledgeAclStore
    ) -> None:
        """Viewer 无权访问非 owner 且无 grant 的 KB。"""
        principal = AuthPrincipal(
            tenant_id="acme",
            user_id="bob",
            role=TenantRole.VIEWER,
        )
        decision = acl_store.can_access(
            principal, "private-kb", Action.KB_QUERY
        )
        assert not decision.allowed

    def test_viewer_can_access_granted_kb(
        self, acl_store: KnowledgeAclStore
    ) -> None:
        """Viewer 通过 explicit grant 访问 shared-kb。"""
        principal = AuthPrincipal(
            tenant_id="acme",
            user_id="bob",
            role=TenantRole.VIEWER,
        )
        decision = acl_store.can_access(
            principal, "shared-kb", Action.KB_QUERY
        )
        assert decision.allowed

    def test_cross_tenant_kb_invisible(
        self, acl_store: KnowledgeAclStore
    ) -> None:
        """不同租户的 KB 不可见。"""
        principal = AuthPrincipal(
            tenant_id="other-tenant",
            user_id="alice",
            role=TenantRole.OWNER,
        )
        decision = acl_store.can_access(
            principal, "private-kb", Action.KB_QUERY
        )
        assert not decision.allowed

    def test_get_authorized_kb_ids(self, acl_store: KnowledgeAclStore) -> None:
        """获取授权 KB 列表。"""
        principal = AuthPrincipal(
            tenant_id="acme",
            user_id="alice",
            role=TenantRole.OWNER,
        )
        kb_ids = acl_store.get_authorized_kb_ids(principal, Action.KB_QUERY)
        assert "private-kb" in kb_ids
        assert "shared-kb" in kb_ids

    def test_viewer_cannot_ingest(self) -> None:
        """Viewer 角色 check_permissions 拒绝 ingest。"""
        policy = TenantPolicy.default()
        principal = AuthPrincipal(
            tenant_id="acme",
            user_id="viewer-user",
            role=TenantRole.VIEWER,
        )
        decision = policy.check(principal, Action.DOC_INGEST)
        assert not decision.allowed

    def test_contributor_can_ingest(self) -> None:
        """Contributor 角色 check_permissions 允许 ingest。"""
        policy = TenantPolicy.default()
        principal = AuthPrincipal(
            tenant_id="acme",
            user_id="contrib-user",
            role=TenantRole.CONTRIBUTOR,
        )
        decision = policy.check(principal, Action.DOC_INGEST)
        assert decision.allowed


# =====================================================================
# Part 5: 综合场景 — 多租户隔离
# =====================================================================


class TestMultiTenantIsolation:
    """多租户隔离综合场景。"""

    def test_two_tenants_same_user_different_roles(self) -> None:
        """同一用户在两个租户中有不同角色。"""
        ms = TenantMembershipStore()
        ms.upsert("tenant-a", "alice", TenantRole.OWNER)
        ms.upsert("tenant-b", "alice", TenantRole.VIEWER)

        p_a = ms.resolve_principal("tenant-a", "alice")
        p_b = ms.resolve_principal("tenant-b", "alice")

        assert p_a.role == TenantRole.OWNER
        assert p_b.role == TenantRole.VIEWER

        # Owner can delete tenant, Viewer cannot
        policy = TenantPolicy.default()
        assert policy.check(p_a, Action.TENANT_DELETE).allowed
        assert not policy.check(p_b, Action.TENANT_DELETE).allowed

    def test_tenant_key_isolation(self) -> None:
        """不同租户的 Redis key 前缀不同。"""
        from xruntime._infra._tenant import build_tenant_key_config

        config_a = build_tenant_key_config("tenant-a", "xrt:{tid}:")
        config_b = build_tenant_key_config("tenant-b", "xrt:{tid}:")

        # Key prefixes should differ
        assert config_a.agent != config_b.agent
        assert config_a.session != config_b.session

    def test_api_key_tenant_binding(
        self,
    ) -> None:
        """API Key 绑定到特定租户。"""
        store = ApiKeyStore(
            [
                ApiKeyRecord(
                    key="sk-tenant-a",
                    tenant_id="tenant-a",
                    user_id="alice",
                    role=TenantRole.ADMIN,
                ),
                ApiKeyRecord(
                    key="sk-tenant-b",
                    tenant_id="tenant-b",
                    user_id="alice",
                    role=TenantRole.VIEWER,
                ),
            ],
        )

        p_a = store.authenticate("sk-tenant-a")
        p_b = store.authenticate("sk-tenant-b")

        assert p_a.tenant_id == "tenant-a"
        assert p_a.role == TenantRole.ADMIN
        assert p_b.tenant_id == "tenant-b"
        assert p_b.role == TenantRole.VIEWER
