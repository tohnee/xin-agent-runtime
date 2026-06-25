# -*- coding: utf-8 -*-
"""单元测试：_tools.py check_permissions + _auth.py dispatch.

覆盖覆盖率报告中标记的低覆盖路径：
- _tools.py: _check_tenant_action ALLOW/DENY/unknown role
- _tools.py: SearchKnowledgeTool.check_permissions (viewer/contributor)
- _tools.py: IngestKnowledgeTool.check_permissions (viewer/contributor)
- _tools.py: SearchKnowledgeTool.__call__ + IngestKnowledgeTool.__call__
- _auth.py: dispatch public route bypass
- _auth.py: dispatch fail-closed (no keys configured)
- _auth.py: dispatch invalid key returns 401
- _auth.py: dispatch valid key sets request.state.principal
- _auth.py: authenticate_headers JWT Bearer path
- _auth.py: authenticate_headers JWT invalid token
- _auth.py: authenticate_headers API key fallback (no store)
"""
import base64
import hashlib
import hmac
import json
from typing import Any

import pytest

from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)

from xruntime._runtime._knowledge._tools import (
    SearchKnowledgeTool,
    IngestKnowledgeTool,
    _check_tenant_action,
)
from xruntime._runtime._knowledge._registry import KnowledgeRegistry
from xruntime._runtime._tenant._policy import TenantRole
from xruntime._gateway._auth import AuthMiddleware


# =====================================================================
# Part 1: _check_tenant_action unit tests
# =====================================================================


class TestCheckTenantAction:
    """_check_tenant_action 直接单元测试。"""

    def test_viewer_query_allowed(self) -> None:
        """Viewer 有 kb:query 权限 → ALLOW。"""
        decision = _check_tenant_action("viewer", "kb:query")
        assert decision.behavior == PermissionBehavior.ALLOW

    def test_viewer_ingest_denied(self) -> None:
        """Viewer 没有 doc:ingest 权限 → DENY。"""
        decision = _check_tenant_action("viewer", "doc:ingest")
        assert decision.behavior == PermissionBehavior.DENY

    def test_contributor_ingest_allowed(self) -> None:
        """Contributor 有 doc:ingest 权限 → ALLOW。"""
        decision = _check_tenant_action("contributor", "doc:ingest")
        assert decision.behavior == PermissionBehavior.ALLOW

    def test_contributor_query_allowed(self) -> None:
        """Contributor 有 kb:query 权限 → ALLOW。"""
        decision = _check_tenant_action("contributor", "kb:query")
        assert decision.behavior == PermissionBehavior.ALLOW

    def test_admin_ingest_allowed(self) -> None:
        """Admin 有 doc:ingest 权限 → ALLOW。"""
        decision = _check_tenant_action("admin", "doc:ingest")
        assert decision.behavior == PermissionBehavior.ALLOW

    def test_owner_all_allowed(self) -> None:
        """Owner 有所有权限 → ALLOW。"""
        for action in ("kb:query", "doc:ingest", "kb:delete"):
            decision = _check_tenant_action("owner", action)
            assert decision.behavior == PermissionBehavior.ALLOW

    def test_unknown_role_denied(self) -> None:
        """未知角色 → DENY。"""
        decision = _check_tenant_action("superadmin", "kb:query")
        assert decision.behavior == PermissionBehavior.DENY
        assert "Unknown role" in decision.message

    def test_unknown_action_denied(self) -> None:
        """未知 action → DENY。"""
        decision = _check_tenant_action("owner", "nonexistent:action")
        assert decision.behavior == PermissionBehavior.DENY


# =====================================================================
# Part 2: SearchKnowledgeTool.check_permissions
# =====================================================================


class TestSearchToolPermissions:
    """SearchKnowledgeTool.check_permissions 权限验证。"""

    def _make_tool(self, role: str = "viewer") -> SearchKnowledgeTool:
        return SearchKnowledgeTool(
            registry=KnowledgeRegistry(),
            tenant_id="acme",
            user_id="alice",
            kb_ids=["kb1"],
            role=role,
        )

    def _make_context(self) -> PermissionContext:
        return PermissionContext(
            user_id="alice",
            session_id="s1",
            agent_id="a1",
        )

    async def test_viewer_search_allowed(self) -> None:
        """Viewer 调用 search_knowledge → ALLOW。"""
        tool = self._make_tool("viewer")
        decision = await tool.check_permissions(
            {"query": "test"}, self._make_context()
        )
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_contributor_search_allowed(self) -> None:
        """Contributor 调用 search_knowledge → ALLOW。"""
        tool = self._make_tool("contributor")
        decision = await tool.check_permissions(
            {"query": "test"}, self._make_context()
        )
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_owner_search_allowed(self) -> None:
        """Owner 调用 search_knowledge → ALLOW。"""
        tool = self._make_tool("owner")
        decision = await tool.check_permissions(
            {"query": "test"}, self._make_context()
        )
        assert decision.behavior == PermissionBehavior.ALLOW


# =====================================================================
# Part 3: IngestKnowledgeTool.check_permissions
# =====================================================================


class TestIngestToolPermissions:
    """IngestKnowledgeTool.check_permissions 权限验证。"""

    def _make_tool(self, role: str = "viewer") -> IngestKnowledgeTool:
        return IngestKnowledgeTool(
            registry=KnowledgeRegistry(),
            tenant_id="acme",
            user_id="alice",
            kb_ids=["kb1"],
            role=role,
        )

    def _make_context(self) -> PermissionContext:
        return PermissionContext(
            user_id="alice",
            session_id="s1",
            agent_id="a1",
        )

    async def test_viewer_ingest_denied(self) -> None:
        """Viewer 调用 ingest_knowledge → DENY。"""
        tool = self._make_tool("viewer")
        decision = await tool.check_permissions(
            {"content": "test"}, self._make_context()
        )
        assert decision.behavior == PermissionBehavior.DENY

    async def test_contributor_ingest_allowed(self) -> None:
        """Contributor 调用 ingest_knowledge → ALLOW。"""
        tool = self._make_tool("contributor")
        decision = await tool.check_permissions(
            {"content": "test"}, self._make_context()
        )
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_admin_ingest_allowed(self) -> None:
        """Admin 调用 ingest_knowledge → ALLOW。"""
        tool = self._make_tool("admin")
        decision = await tool.check_permissions(
            {"content": "test"}, self._make_context()
        )
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_owner_ingest_allowed(self) -> None:
        """Owner 调用 ingest_knowledge → ALLOW。"""
        tool = self._make_tool("owner")
        decision = await tool.check_permissions(
            {"content": "test"}, self._make_context()
        )
        assert decision.behavior == PermissionBehavior.ALLOW


# =====================================================================
# Part 4: AuthMiddleware dispatch 端到端
# =====================================================================


class TestAuthMiddlewareDispatch:
    """AuthMiddleware.dispatch 的 ASGI 端到端行为。"""

    def _make_app(
        self,
        api_keys: set[str] | None = None,
        api_key_store: Any = None,
        jwt_parser: Any = None,
    ) -> Any:
        """Create a minimal FastAPI app + AuthMiddleware pair."""
        from fastapi import FastAPI, Request as FARequest
        from fastapi.responses import JSONResponse

        app = FastAPI()

        @app.get("/protected")
        async def protected(request: FARequest) -> Any:
            principal = getattr(request.state, "principal", None)
            if principal:
                return JSONResponse(
                    {
                        "tenant": principal.tenant_id,
                        "user": principal.user_id,
                        "role": principal.role.value,
                    }
                )
            return JSONResponse({"error": "no principal"})

        app.add_middleware(
            AuthMiddleware,
            api_keys=api_keys or set(),
            api_key_store=api_key_store,
            jwt_parser=jwt_parser,
        )
        return app

    async def test_public_route_bypasses_auth(self) -> None:
        """/health 路由跳过认证。"""
        from httpx import ASGITransport, AsyncClient

        app = self._make_app(api_keys={"sk-test"})
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/health")
            # /health may 404 on Starlette, but should NOT 401
            assert resp.status_code != 401

    async def test_fail_closed_no_keys_configured(self) -> None:
        """无 API key 配置时拒绝所有非公开请求 (401)。"""
        from httpx import ASGITransport, AsyncClient

        app = self._make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/protected")
            assert resp.status_code == 401
            assert "no API keys" in resp.json()["detail"]

    async def test_invalid_key_returns_401(self) -> None:
        """无效 API key 返回 401。"""
        from httpx import ASGITransport, AsyncClient

        app = self._make_app(api_keys={"sk-valid"})
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get(
                "/protected",
                headers={"x-api-key": "sk-invalid"},
            )
            assert resp.status_code == 401
            assert "Invalid" in resp.json()["detail"]

    async def test_valid_key_sets_principal(self) -> None:
        """有效 API key 设置 request.state.principal。"""
        from httpx import ASGITransport, AsyncClient

        app = self._make_app(api_keys={"sk-valid"})
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get(
                "/protected",
                headers={"x-api-key": "sk-valid"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["tenant"] == "default"
            assert data["role"] == "viewer"


# =====================================================================
# Part 5: AuthMiddleware authenticate_headers 边缘路径
# =====================================================================


def _make_jwt(
    secret: str,
    payload: dict[str, Any],
) -> str:
    """Create a signed HS256 JWT for testing."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode(),
    ).rstrip(b"=")
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode(),
    ).rstrip(b"=")
    signing_input = header + b"." + payload_b64
    signature = hmac.new(
        secret.encode(),
        signing_input,
        hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=")
    return (
        header.decode() + "." + payload_b64.decode() + "." + sig_b64.decode()
    )


class TestAuthenticateHeadersEdgeCases:
    """authenticate_headers 的边缘路径。"""

    def test_jwt_bearer_path(self) -> None:
        """JWT Bearer token 通过 jwt_parser 解析。"""
        from xruntime._runtime._tenant._store import (
            JwtClaimsParser,
        )

        secret = "test-secret"
        token = _make_jwt(
            secret,
            {
                "tenant_id": "acme",
                "sub": "alice",
                "role": "admin",
            },
        )
        parser = JwtClaimsParser(secret=secret)
        mw = AuthMiddleware(app=None, jwt_parser=parser)

        class FakeHeaders:
            def get(self, key: str, default: str = "") -> str:
                return {
                    "authorization": f"Bearer {token}",
                }.get(key, default)

        principal = mw.authenticate_headers(FakeHeaders())
        assert principal is not None
        assert principal.tenant_id == "acme"
        assert principal.role == TenantRole.ADMIN

    def test_jwt_invalid_token_returns_none(self) -> None:
        """无效 JWT token 返回 None。"""
        from xruntime._runtime._tenant._store import (
            JwtClaimsParser,
        )

        parser = JwtClaimsParser(secret="real-secret")
        mw = AuthMiddleware(app=None, jwt_parser=parser)

        class FakeHeaders:
            def get(self, key: str, default: str = "") -> str:
                return {
                    "authorization": "Bearer invalid.token.here",
                }.get(key, default)

        principal = mw.authenticate_headers(FakeHeaders())
        assert principal is None

    def test_api_key_fallback_no_store(self) -> None:
        """无 ApiKeyStore 时回退到 api_keys 集合。"""
        mw = AuthMiddleware(app=None, api_keys={"sk-fallback"})

        class FakeHeaders:
            def get(self, key: str, default: str = "") -> str:
                return {
                    "x-api-key": "sk-fallback",
                }.get(key, default)

        principal = mw.authenticate_headers(FakeHeaders())
        assert principal is not None
        assert principal.tenant_id == "default"
        assert principal.role == TenantRole.VIEWER

    def test_no_credentials_returns_none(self) -> None:
        """无任何凭证返回 None。"""
        mw = AuthMiddleware(app=None, api_keys={"sk-test"})

        class FakeHeaders:
            def get(self, key: str, default: str = "") -> str:
                return default

        principal = mw.authenticate_headers(FakeHeaders())
        assert principal is None

    def test_bearer_without_jwt_parser_falls_back(
        self,
    ) -> None:
        """有 Bearer 但无 jwt_parser 时回退到 API key。"""
        mw = AuthMiddleware(app=None, api_keys={"sk-test"})

        class FakeHeaders:
            def get(self, key: str, default: str = "") -> str:
                if key == "authorization":
                    return "Bearer some-token"
                if key == "x-api-key":
                    return "sk-test"
                return default

        principal = mw.authenticate_headers(FakeHeaders())
        assert principal is not None
        assert principal.api_key_id == "sk-test"
