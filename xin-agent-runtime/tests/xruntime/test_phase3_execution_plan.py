# -*- coding: utf-8 -*-
"""Tests for Phase 3: M4 RuntimeExecutionPlan."""
import pytest

from xruntime._gateway._plan import (
    RuntimeExecutionPlan,
    WorkspacePolicy,
    KnowledgeScope,
    build_plan_from_request,
)
from xruntime._gateway._request import (
    ProtocolType,
    XRuntimeRequest,
    ToolExecutionMode,
)


class TestRuntimeExecutionPlanModel:
    """RuntimeExecutionPlan model field completeness."""

    def test_plan_creation(self) -> None:
        """A plan can be created with all required fields."""
        plan = RuntimeExecutionPlan(
            protocol=ProtocolType.ANTHROPIC,
            tenant_id="acme",
            user_id="alice",
            session_id="s1",
            agent_name="assistant",
            prompt="Hello",
        )
        assert plan.protocol == ProtocolType.ANTHROPIC
        assert plan.tenant_id == "acme"
        assert plan.prompt == "Hello"

    def test_plan_defaults(self) -> None:
        """Plan defaults are sensible."""
        plan = RuntimeExecutionPlan(
            protocol=ProtocolType.CLAUDE_CODE,
            tenant_id="t1",
            user_id="u1",
            agent_name="agent",
            prompt="test",
        )
        assert plan.max_turns is None
        assert plan.max_budget_usd is None
        assert plan.permission_mode == "default"
        assert plan.allowed_tools == []
        assert plan.workspace_policy.backend == "local"


class TestBuildPlanFromRequest:
    """build_plan_from_request maps XRuntimeRequest fields to plan."""

    def test_basic_mapping(self) -> None:
        """Basic fields map correctly."""
        req = XRuntimeRequest(
            protocol=ProtocolType.ANTHROPIC,
            tenant_id="acme",
            user_id="alice",
            prompt="Hello world",
        )
        plan = build_plan_from_request(req)
        assert plan.protocol == ProtocolType.ANTHROPIC
        assert plan.tenant_id == "acme"
        assert plan.user_id == "alice"
        assert plan.prompt == "Hello world"

    def test_claude_code_metadata_mapping(self) -> None:
        """Claude Code metadata fields map to plan fields."""
        req = XRuntimeRequest(
            protocol=ProtocolType.CLAUDE_CODE,
            tenant_id="acme",
            user_id="alice",
            prompt="Write code",
            metadata={
                "sandbox": "docker",
                "max_budget_usd": 10.0,
                "model": "claude-sonnet",
                "fallback_model": "claude-haiku",
                "max_turns": 20,
                "allowed_tools": ["Read", "Write", "Bash"],
                "disallowed_tools": ["rm"],
            },
        )
        plan = build_plan_from_request(req)
        assert plan.workspace_policy.backend == "docker"
        assert plan.max_budget_usd == 10.0
        assert plan.model_config_name == "claude-sonnet"
        assert plan.fallback_model_config_name == "claude-haiku"
        assert plan.max_turns == 20
        assert "Read" in plan.allowed_tools
        assert "Bash" in plan.allowed_tools
        assert "rm" in plan.disallowed_tools

    def test_permissions_can_only_tighten(self) -> None:
        """Client permissions cannot widen beyond tenant policy."""
        req = XRuntimeRequest(
            protocol=ProtocolType.OPENCODE,
            tenant_id="acme",
            user_id="viewer-user",
            prompt="test",
            metadata={
                "allowed_tools": [
                    "Read",
                    "Write",
                    "Bash",
                    "Admin",
                ],
            },
        )
        tenant_allowed = {
            "Read",
            "Glob",
            "Grep",
            "search_knowledge",
        }
        plan = build_plan_from_request(
            req,
            tenant_tool_allowlist=tenant_allowed,
        )
        assert "Read" in plan.allowed_tools
        assert "Write" not in plan.allowed_tools
        assert "Bash" not in plan.allowed_tools
        assert "Admin" not in plan.allowed_tools

    def test_no_tenant_allowlist_passes_through(self) -> None:
        """Without a tenant allowlist, client tools pass through."""
        req = XRuntimeRequest(
            protocol=ProtocolType.CLAUDE_CODE,
            tenant_id="acme",
            user_id="admin-user",
            prompt="test",
            metadata={
                "allowed_tools": ["Read", "Write", "Bash"],
            },
        )
        plan = build_plan_from_request(req)
        assert "Read" in plan.allowed_tools
        assert "Write" in plan.allowed_tools
        assert "Bash" in plan.allowed_tools


class TestWorkspacePolicy:
    """WorkspacePolicy maps sandbox config."""

    def test_docker_backend(self) -> None:
        """sandbox=docker maps to docker backend."""
        wp = WorkspacePolicy(backend="docker")
        assert wp.backend == "docker"

    def test_local_backend(self) -> None:
        """sandbox=local maps to local backend."""
        wp = WorkspacePolicy(backend="local")
        assert wp.backend == "local"

    def test_default_is_local(self) -> None:
        """Default workspace policy is local."""
        wp = WorkspacePolicy()
        assert wp.backend == "local"


class TestKnowledgeScope:
    """KnowledgeScope carries authorized KB ids."""

    def test_empty_scope(self) -> None:
        """Empty scope means all tenant KBs eligible."""
        ks = KnowledgeScope(kb_ids=[])
        assert ks.kb_ids == []

    def test_scoped(self) -> None:
        """Scoped KB ids are carried."""
        ks = KnowledgeScope(kb_ids=["kb1", "kb2"])
        assert ks.kb_ids == ["kb1", "kb2"]
