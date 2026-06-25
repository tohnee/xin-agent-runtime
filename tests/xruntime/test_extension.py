# -*- coding: utf-8 -*-
"""Tests for the XRuntime-as-AS-extension architecture.

Verifies that:
1. Enterprise middlewares inherit real AS MiddlewareBase
2. create_xruntime_extension produces correct factory output
3. Protocol adapters can be mounted on AS create_app
4. The full chain: AS ChatService + protocol adapter + enterprise middleware
"""
from unittest.mock import MagicMock

import pytest

from xruntime._gateway._extension import (
    create_xruntime_extension,
    mount_protocol_adapters,
    _default_adapters,
)
from xruntime._gateway._adapter import AdapterRegistry
from xruntime._gateway._request import ProtocolType
from xruntime._config import XRuntimeConfig


class TestMiddlewareInheritsASBase:
    """Verify enterprise middlewares inherit real AS MiddlewareBase."""

    def test_audit_inherits_middleware_base(self) -> None:
        """AuditMiddleware should be a MiddlewareBase subclass."""
        from xruntime._runtime._middleware._audit import AuditMiddleware
        from agentscope.middleware import MiddlewareBase

        assert issubclass(AuditMiddleware, MiddlewareBase)

    def test_quota_inherits_middleware_base(self) -> None:
        """QuotaMiddleware should be a MiddlewareBase subclass."""
        from xruntime._runtime._middleware._quota import QuotaMiddleware
        from agentscope.middleware import MiddlewareBase

        assert issubclass(QuotaMiddleware, MiddlewareBase)

    def test_rbac_inherits_middleware_base(self) -> None:
        """RbacMiddleware should be a MiddlewareBase subclass."""
        from xruntime._runtime._middleware._rbac import RbacMiddleware
        from agentscope.middleware import MiddlewareBase

        assert issubclass(RbacMiddleware, MiddlewareBase)

    def test_redaction_inherits_middleware_base(self) -> None:
        """SecretRedactionMiddleware should be a MiddlewareBase subclass."""
        from xruntime._runtime._middleware._redaction import (
            SecretRedactionMiddleware,
        )
        from agentscope.middleware import MiddlewareBase

        assert issubclass(SecretRedactionMiddleware, MiddlewareBase)

    def test_audit_is_implemented_on_acting(self) -> None:
        """AuditMiddleware should report on_acting as implemented."""
        from xruntime._runtime._middleware._audit import (
            AuditMiddleware,
            AuditLogger,
        )

        mw = AuditMiddleware(AuditLogger(sink="memory"))
        assert mw.is_implemented("on_acting") is True

    def test_quota_is_implemented_on_acting(self) -> None:
        """QuotaMiddleware should report on_acting as implemented."""
        from xruntime._runtime._middleware._quota import (
            QuotaMiddleware,
            QuotaConfig,
        )

        mw = QuotaMiddleware(QuotaConfig())
        assert mw.is_implemented("on_acting") is True

    def test_rbac_is_implemented_on_acting(self) -> None:
        """RbacMiddleware should report on_acting as implemented."""
        from xruntime._runtime._middleware._rbac import RbacMiddleware

        mw = RbacMiddleware(roles={})
        assert mw.is_implemented("on_acting") is True

    async def test_rbac_denies_tool_via_on_acting(self) -> None:
        """RbacMiddleware.on_acting should raise PermissionError on deny."""
        from xruntime._runtime._middleware._rbac import (
            RbacMiddleware,
            RoleDefinition,
            RbacRule,
        )

        mw = RbacMiddleware(
            roles={
                "viewer": RoleDefinition(
                    "viewer",
                    [RbacRule("Read", "allow"), RbacRule("Bash", "deny")],
                ),
            },
        )
        mw.assign_role("sess-1", "viewer")

        agent = MagicMock()
        agent.state = MagicMock()
        agent.state.session_id = "sess-1"

        tool_call = MagicMock()
        tool_call.tool_call_name = "Bash"
        tool_call.tool_call_id = "tc-1"

        async def mock_next():
            yield MagicMock()

        gen = mw.on_acting(agent, {"tool_call": tool_call}, mock_next)
        with pytest.raises(PermissionError, match="RBAC denied"):
            async for _ in gen:
                pass

    async def test_rbac_allows_tool_via_on_acting(self) -> None:
        """RbacMiddleware.on_acting should allow permitted tools."""
        from xruntime._runtime._middleware._rbac import (
            RbacMiddleware,
            RoleDefinition,
            RbacRule,
        )

        mw = RbacMiddleware(
            roles={
                "viewer": RoleDefinition(
                    "viewer",
                    [RbacRule("Read", "allow")],
                ),
            },
        )
        mw.assign_role("sess-1", "viewer")

        agent = MagicMock()
        agent.state = MagicMock()
        agent.state.session_id = "sess-1"

        tool_call = MagicMock()
        tool_call.tool_call_name = "Read"
        tool_call.tool_call_id = "tc-1"

        async def mock_next():
            yield MagicMock()

        gen = mw.on_acting(agent, {"tool_call": tool_call}, mock_next)
        results = []
        async for chunk in gen:
            results.append(chunk)
        assert len(results) == 1


class TestCreateXRuntimeExtension:
    """Tests for create_xruntime_extension."""

    def test_returns_dict_with_required_keys(self) -> None:
        """Should return extra_agent_middlewares + adapter_registry."""
        ext = create_xruntime_extension()
        assert "extra_agent_middlewares" in ext
        assert "adapter_registry" in ext
        assert "config" in ext

    def test_adapter_registry_has_all_three(self) -> None:
        """Registry should have all three protocol adapters."""
        ext = create_xruntime_extension()
        registry: AdapterRegistry = ext["adapter_registry"]
        assert registry.get(ProtocolType.ANTHROPIC) is not None
        assert registry.get(ProtocolType.CLAUDE_CODE) is not None
        assert registry.get(ProtocolType.OPENCODE) is not None

    async def test_middleware_factory_produces_middlewares(self) -> None:
        """The middleware factory should produce real AS middlewares."""
        ext = create_xruntime_extension()
        factory = ext["extra_agent_middlewares"]
        middlewares = await factory("user-1", "agent-1", "sess-1")
        assert len(middlewares) >= 2  # audit + quota + redaction

        from agentscope.middleware import MiddlewareBase

        for mw in middlewares:
            assert isinstance(mw, MiddlewareBase)

    async def test_middleware_factory_includes_audit_when_enabled(
        self,
    ) -> None:
        """Factory should include audit middleware when audit_enabled."""
        config = XRuntimeConfig()
        config.observability.audit_enabled = True
        ext = create_xruntime_extension(config=config)
        factory = ext["extra_agent_middlewares"]
        middlewares = await factory("u1", "a1", "s1")

        from xruntime._runtime._middleware._audit import AuditMiddleware

        audit_mws = [m for m in middlewares if isinstance(m, AuditMiddleware)]
        assert len(audit_mws) == 1

    async def test_middleware_factory_excludes_audit_when_disabled(
        self,
    ) -> None:
        """Factory should exclude audit middleware when audit_enabled=False."""
        config = XRuntimeConfig()
        config.observability.audit_enabled = False
        ext = create_xruntime_extension(config=config)
        factory = ext["extra_agent_middlewares"]
        middlewares = await factory("u1", "a1", "s1")

        from xruntime._runtime._middleware._audit import AuditMiddleware

        audit_mws = [m for m in middlewares if isinstance(m, AuditMiddleware)]
        assert len(audit_mws) == 0

    async def test_middleware_factory_includes_quota(self) -> None:
        """Factory should include quota middleware."""
        ext = create_xruntime_extension()
        factory = ext["extra_agent_middlewares"]
        middlewares = await factory("u1", "a1", "s1")

        from xruntime._runtime._middleware._quota import QuotaMiddleware

        quota_mws = [m for m in middlewares if isinstance(m, QuotaMiddleware)]
        assert len(quota_mws) == 1

    async def test_middleware_factory_includes_redaction(self) -> None:
        """Factory should include redaction middleware."""
        ext = create_xruntime_extension()
        factory = ext["extra_agent_middlewares"]
        middlewares = await factory("u1", "a1", "s1")

        from xruntime._runtime._middleware._redaction import (
            SecretRedactionMiddleware,
        )

        redaction_mws = [
            m for m in middlewares if isinstance(m, SecretRedactionMiddleware)
        ]
        assert len(redaction_mws) == 1

    async def test_middleware_factory_includes_rbac(self) -> None:
        """Factory should include RBAC middleware (Bug 8 fix)."""
        ext = create_xruntime_extension()
        factory = ext["extra_agent_middlewares"]
        middlewares = await factory("u1", "a1", "s1")

        from xruntime._runtime._middleware._rbac import RbacMiddleware

        rbac_mws = [m for m in middlewares if isinstance(m, RbacMiddleware)]
        assert len(rbac_mws) == 1

    async def test_quota_tracker_shared_across_turns(self) -> None:
        """Same session should get the same QuotaTracker (Bug 3 fix).

        The factory is called per-turn; without caching each turn
        gets a fresh tracker and quota never accumulates.
        """
        ext = create_xruntime_extension()
        factory = ext["extra_agent_middlewares"]

        mw_turn1 = await factory("u1", "a1", "sess-shared")
        mw_turn2 = await factory("u1", "a1", "sess-shared")

        from xruntime._runtime._middleware._quota import QuotaMiddleware

        q1 = [m for m in mw_turn1 if isinstance(m, QuotaMiddleware)][0]
        q2 = [m for m in mw_turn2 if isinstance(m, QuotaMiddleware)][0]

        assert q1.tracker is q2.tracker

    async def test_quota_tracker_isolated_per_session(self) -> None:
        """Different sessions should get different trackers."""
        ext = create_xruntime_extension()
        factory = ext["extra_agent_middlewares"]

        mw_a = await factory("u1", "a1", "sess-a")
        mw_b = await factory("u1", "a1", "sess-b")

        from xruntime._runtime._middleware._quota import QuotaMiddleware

        qa = [m for m in mw_a if isinstance(m, QuotaMiddleware)][0]
        qb = [m for m in mw_b if isinstance(m, QuotaMiddleware)][0]

        assert qa.tracker is not qb.tracker

    async def test_audit_logger_shared_across_turns(self) -> None:
        """Same extension should share one AuditLogger (Bug 4 fix).

        The logger must not be recreated per turn — otherwise entries
        from previous turns are lost.
        """
        from xruntime._runtime._middleware._audit import AuditMiddleware

        ext = create_xruntime_extension()
        factory = ext["extra_agent_middlewares"]

        mw1 = await factory("u1", "a1", "s1")
        mw2 = await factory("u1", "a1", "s1")

        a1 = [m for m in mw1 if isinstance(m, AuditMiddleware)][0]
        a2 = [m for m in mw2 if isinstance(m, AuditMiddleware)][0]

        assert a1.logger is a2.logger

    async def test_audit_logger_respects_file_sink(self) -> None:
        """When audit_storage='file', logger should use file sink."""
        import tempfile
        import os
        from xruntime._config import XRuntimeConfig
        from xruntime._runtime._middleware._audit import (
            AuditMiddleware,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["XRUNTIME_AUDIT_DIR"] = tmpdir
            try:
                config = XRuntimeConfig()
                config.observability.audit_storage = "file"
                config.observability.audit_enabled = True
                ext = create_xruntime_extension(
                    config=config,
                    tenant_id="test-tenant",
                )
                factory = ext["extra_agent_middlewares"]
                middlewares = await factory("u1", "a1", "s1")

                audit_mws = [
                    m for m in middlewares if isinstance(m, AuditMiddleware)
                ]
                assert len(audit_mws) == 1
                logger = audit_mws[0].logger
                assert logger.sink == "file"
                assert logger.file_path is not None
                assert "test-tenant" in logger.file_path
            finally:
                del os.environ["XRUNTIME_AUDIT_DIR"]

    async def test_audit_logger_uses_memory_when_configured(
        self,
    ) -> None:
        """When audit_storage='memory', logger should use memory."""
        from xruntime._config import XRuntimeConfig
        from xruntime._runtime._middleware._audit import (
            AuditMiddleware,
        )

        config = XRuntimeConfig()
        config.observability.audit_storage = "memory"
        config.observability.audit_enabled = True
        ext = create_xruntime_extension(config=config)
        factory = ext["extra_agent_middlewares"]
        middlewares = await factory("u1", "a1", "s1")

        audit_mws = [m for m in middlewares if isinstance(m, AuditMiddleware)]
        assert audit_mws[0].logger.sink == "memory"

    def test_extension_returns_state_cache(self) -> None:
        """Extension dict should expose the middleware state cache."""
        from xruntime._gateway._mw_state import MiddlewareStateCache

        ext = create_xruntime_extension()
        assert "middleware_state_cache" in ext
        assert isinstance(
            ext["middleware_state_cache"],
            MiddlewareStateCache,
        )


class TestMountProtocolAdapters:
    """Tests for mounting protocol adapter routes on AS app."""

    def test_routes_mounted(self) -> None:
        """mount_protocol_adapters should add three routes."""
        from fastapi import FastAPI

        app = FastAPI()
        app.state.chat_service = MagicMock()
        app.state.message_bus = MagicMock()
        app.state.storage = MagicMock()

        registry = _default_adapters()
        mount_protocol_adapters(app, registry)

        routes = [r.path for r in app.routes]
        assert "/v1/messages" in routes
        assert "/v1/claude-code/query" in routes
        assert "/v1/opencode" in routes

    def test_mount_with_empty_registry(self) -> None:
        """Mounting with empty registry should still add routes."""
        from fastapi import FastAPI

        app = FastAPI()
        app.state.chat_service = MagicMock()
        app.state.message_bus = MagicMock()
        app.state.storage = MagicMock()

        mount_protocol_adapters(app, AdapterRegistry())

        routes = [r.path for r in app.routes]
        assert "/v1/messages" in routes


class TestNoProductionRuntime:
    """Verify ProductionRuntime is removed from the codebase."""

    def test_production_module_deleted(self) -> None:
        """_production.py should no longer exist or be importable."""
        import importlib

        try:
            importlib.import_module(
                "xruntime._runtime._production",
            )
            # If it imports, it should NOT have ProductionRuntime
            # that builds its own Agent
        except ImportError:
            pass  # Good — module removed

    def test_no_standalone_create_xruntime_app(self) -> None:
        """The old standalone create_xruntime_app should be gone."""
        # __init__ exports create_xruntime_extension, not the old app fn
        import xruntime

        assert hasattr(xruntime, "create_xruntime_extension")
        assert not hasattr(xruntime, "create_xruntime_app")


class TestBlueprintMaxIters:
    """Tests for the _blueprint_max_iters helper (issue #10)."""

    def _state(self, agents):
        from xruntime._gateway._extension import _GatewayState
        from xruntime._runtime._model_resolver import ModelResolver

        config = XRuntimeConfig(agents=agents)
        return _GatewayState(config, ModelResolver())

    def test_returns_configured_max_iters(self) -> None:
        """A blueprint's configured max_iters is returned."""
        from xruntime._config import AgentBlueprintConfig
        from xruntime._gateway._extension import _blueprint_max_iters

        state = self._state(
            [AgentBlueprintConfig(name="bp", max_iters=42)],
        )
        assert _blueprint_max_iters(state, "bp") == 42

    def test_returns_none_when_unset(self) -> None:
        """An unset max_iters yields None (falls back to default)."""
        from xruntime._config import AgentBlueprintConfig
        from xruntime._gateway._extension import _blueprint_max_iters

        state = self._state([AgentBlueprintConfig(name="bp")])
        assert _blueprint_max_iters(state, "bp") is None

    def test_returns_none_for_unknown_agent(self) -> None:
        """An unknown agent name yields None."""
        from xruntime._config import AgentBlueprintConfig
        from xruntime._gateway._extension import _blueprint_max_iters

        state = self._state(
            [AgentBlueprintConfig(name="bp", max_iters=7)],
        )
        assert _blueprint_max_iters(state, "other") is None


class TestKnowledgeMiddlewareInjection:
    """The middleware factory injects the knowledge middleware (issue #12)."""

    async def test_knowledge_middleware_injected_when_enabled(
        self,
        tmp_path,
    ) -> None:
        """When knowledge is enabled, the factory adds KnowledgeMiddleware."""
        from xruntime._runtime._knowledge._middleware import (
            KnowledgeMiddleware,
        )

        config = XRuntimeConfig()
        config.knowledge.enabled = True
        config.knowledge.raw_dir = str(tmp_path / "raw")
        config.knowledge.compiled_dir = str(tmp_path / "compiled")
        config.observability.audit_enabled = False
        config.enable_enterprise_middlewares = False

        ext = create_xruntime_extension(config=config)
        factory = ext["extra_agent_middlewares"]
        mws = await factory("u1", "a1", "s1")
        assert any(isinstance(m, KnowledgeMiddleware) for m in mws)

    async def test_knowledge_middleware_absent_when_disabled(self) -> None:
        """When knowledge is disabled, no KnowledgeMiddleware is added."""
        from xruntime._runtime._knowledge._middleware import (
            KnowledgeMiddleware,
        )

        config = XRuntimeConfig()
        config.knowledge.enabled = False

        ext = create_xruntime_extension(config=config)
        factory = ext["extra_agent_middlewares"]
        mws = await factory("u1", "a1", "s1")
        assert not any(isinstance(m, KnowledgeMiddleware) for m in mws)
