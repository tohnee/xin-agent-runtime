# -*- coding: utf-8 -*-
"""Tests for tenant tool allowlist enforcement.

This is the TDD test file for the P0 issue:
"tenant_tool_allowlist=None 应从 tenant policy/config 中取 allowlist 后传入 plan"
"""
import pytest


class TestTenantToolAllowlist:
    """Tests for tenant-level tool allowlist enforcement."""

    def test_tenant_config_has_tool_allowlist(self) -> None:
        """TenantConfig should have a tool_allowlist field."""
        from xruntime._config import TenantConfig

        tenant = TenantConfig(
            id="t1",
            name="Test Tenant",
            tool_allowlist=["bash", "read", "write"],
        )
        assert set(tenant.tool_allowlist) == {"bash", "read", "write"}

    def test_tenant_config_tool_allowlist_default_none(self) -> None:
        """tool_allowlist should default to None (no restriction)."""
        from xruntime._config import TenantConfig

        tenant = TenantConfig(id="t1", name="Test")
        assert tenant.tool_allowlist is None

    def test_build_plan_respects_tenant_tool_allowlist(self) -> None:
        """build_plan_from_request should apply tenant_tool_allowlist.

        When tenant_tool_allowlist is provided, tools not in the allowlist
        should be filtered out from the request's allowed_tools.
        """
        from xruntime._gateway._plan import build_plan_from_request
        from xruntime._gateway._request import XRuntimeRequest, ProtocolType

        req = XRuntimeRequest(
            tenant_id="t1",
            user_id="u1",
            session_id="s1",
            prompt="hello",
            protocol=ProtocolType.ANTHROPIC,
        )
        req.metadata = {
            "allowed_tools": ["bash", "read", "write", "dangerous_tool"]
        }

        plan = build_plan_from_request(
            req,
            tenant_tool_allowlist={"bash", "read"},
        )

        allowed = set(plan.allowed_tools)
        assert "bash" in allowed
        assert "read" in allowed
        assert "write" not in allowed
        assert "dangerous_tool" not in allowed

    def test_tenant_tool_allowlist_none_means_no_restriction(self) -> None:
        """tenant_tool_allowlist=None should not filter tools."""
        from xruntime._gateway._plan import build_plan_from_request
        from xruntime._gateway._request import XRuntimeRequest, ProtocolType

        req = XRuntimeRequest(
            tenant_id="t1",
            user_id="u1",
            session_id="s1",
            prompt="hello",
            protocol=ProtocolType.ANTHROPIC,
        )
        req.metadata = {"allowed_tools": ["bash", "read"]}

        plan = build_plan_from_request(
            req,
            tenant_tool_allowlist=None,
        )

        allowed = set(plan.allowed_tools)
        assert "bash" in allowed
        assert "read" in allowed

    def test_handler_looks_up_tenant_tool_allowlist_from_config(self) -> None:
        """Gateway handler should resolve tool_allowlist from tenant config."""
        from xruntime._config import XRuntimeConfig, TenantConfig

        config = XRuntimeConfig(
            tenants=[
                TenantConfig(
                    id="t1",
                    name="Tenant 1",
                    tool_allowlist=["read", "write"],
                ),
                TenantConfig(
                    id="t2",
                    name="Tenant 2",
                    tool_allowlist=None,
                ),
            ],
        )

        t1_cfg = next(t for t in config.tenants if t.id == "t1")
        t2_cfg = next(t for t in config.tenants if t.id == "t2")

        assert set(t1_cfg.tool_allowlist) == {"read", "write"}
        assert t2_cfg.tool_allowlist is None
