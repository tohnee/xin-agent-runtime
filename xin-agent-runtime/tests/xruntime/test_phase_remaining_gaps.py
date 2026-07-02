# -*- coding: utf-8 -*-
"""Tests for remaining gaps: plan wiring, OpenCode schema,
frontmatter, budget enforcement.
"""
import pytest

from xruntime._gateway._plan import (
    RuntimeExecutionPlan,
    build_plan_from_request,
)
from xruntime._gateway._request import (
    ProtocolType,
    XRuntimeRequest,
)


class TestPlanWiredInGateway:
    """Verify build_plan_from_request is importable from extension."""

    def test_plan_importable_from_extension(self) -> None:
        """The gateway handler imports build_plan_from_request."""
        import inspect

        from xruntime._gateway import _extension

        src = inspect.getsource(_extension)
        assert "build_plan_from_request" in src

    def test_plan_built_with_principal_kb_ids(self) -> None:
        """Plan carries authorized KB ids from principal."""
        req = XRuntimeRequest(
            protocol=ProtocolType.ANTHROPIC,
            tenant_id="acme",
            user_id="alice",
            prompt="test",
        )
        plan = build_plan_from_request(
            req,
            authorized_kb_ids=["kb1", "kb2"],
        )
        assert plan.knowledge_scope.kb_ids == ["kb1", "kb2"]


class TestOpenCodeSchemaValidation:
    """OpenCode config JSON Schema validation."""

    def test_valid_opencode_config_passes(self) -> None:
        """A valid OpenCode config passes validation."""
        from xruntime._gateway._opencode_schema import (
            validate_opencode_config,
        )

        config = {
            "agents": [
                {
                    "name": "coder",
                    "model": "claude-sonnet",
                    "system_prompt": "You are a coder.",
                },
            ],
            "permissions": {
                "allow": ["Read", "Write"],
                "deny": ["Bash"],
            },
        }
        errors = validate_opencode_config(config)
        assert errors == []

    def test_invalid_opencode_config_rejected(self) -> None:
        """An invalid OpenCode config returns errors."""
        from xruntime._gateway._opencode_schema import (
            validate_opencode_config,
        )

        config = {
            "agents": "not_a_list",  # should be list
            "permissions": {
                "allow": "not_a_list",  # should be list
            },
        }
        errors = validate_opencode_config(config)
        assert len(errors) > 0

    def test_opencode_permissions_cannot_widen(self) -> None:
        """OpenCode permissions can only tighten, not widen."""
        from xruntime._gateway._opencode_schema import (
            tighten_permissions,
        )

        client_perms = {
            "allow": ["Read", "Write", "Bash", "Admin"],
            "deny": [],
        }
        tenant_allowlist = {"Read", "Glob", "Grep"}
        tightened = tighten_permissions(client_perms, tenant_allowlist)
        assert "Read" in tightened["allow"]
        assert "Write" not in tightened["allow"]
        assert "Bash" not in tightened["allow"]
        assert "Admin" not in tightened["allow"]


class TestMarkdownFrontmatter:
    """LLM-Wiki compiled pages have YAML frontmatter."""

    async def test_frontmatter_contains_metadata(self, tmp_path) -> None:
        """Compiled wiki pages have frontmatter with tenant/kb/source."""
        from xruntime._runtime._knowledge._base import (
            KnowledgeBaseConfig,
            KnowledgeQuery,
        )
        from xruntime._runtime._knowledge._llm_wiki_adapter import (
            LlmWikiAdapter,
        )

        config = KnowledgeBaseConfig(
            backend="llm_wiki",
            raw_dir=str(tmp_path / "raw"),
            compiled_dir=str(tmp_path / "compiled"),
            extra={"scoped_layout": True},
        )
        adapter = LlmWikiAdapter(config=config)
        await adapter.initialize()
        await adapter.ingest(
            source_id="doc1",
            content="Test content",
            title="Test",
            metadata={"tenant_id": "acme", "kb_id": "kb1"},
        )
        await adapter.compile()

        # Find the compiled wiki file
        import os

        wiki_dir = os.path.join(
            str(tmp_path),
            "raw",
            "tenants",
            "acme",
            "kbs",
            "kb1",
            "wiki",
        )
        wiki_files = [f for f in os.listdir(wiki_dir) if f.endswith(".md")]
        assert len(wiki_files) >= 1

        with open(os.path.join(wiki_dir, wiki_files[0])) as f:
            content = f.read()

        # Should start with --- frontmatter
        assert content.startswith("---\n")
        # Should contain key metadata
        assert "tenant_id:" in content
        assert "kb_id:" in content
        assert "source_id:" in content


class TestBudgetEnforcement:
    """max_budget_usd from plan enforces cost limit."""

    def test_budget_exceeded_raises(self) -> None:
        """Exceeding max_cost_usd raises QuotaExceededError."""
        from xruntime._runtime._middleware._quota import (
            QuotaConfig,
            QuotaTracker,
            QuotaExceededError,
        )

        tracker = QuotaTracker(
            QuotaConfig(max_cost_usd=1.0),
        )
        # Consume $0.50 — should be fine
        tracker.consume_cost(0.5)
        assert tracker.cost_usd == 0.5

        # Consume another $0.60 — total $1.10, over $1.0 budget
        with pytest.raises(QuotaExceededError):
            tracker.consume_cost(0.6)

    def test_budget_not_exceeded_ok(self) -> None:
        """Within budget consumption is fine."""
        from xruntime._runtime._middleware._quota import (
            QuotaConfig,
            QuotaTracker,
        )

        tracker = QuotaTracker(QuotaConfig(max_cost_usd=10.0))
        tracker.consume_cost(5.0)
        assert tracker.cost_usd == 5.0

    def test_no_budget_unlimited(self) -> None:
        """Without a budget, consumption is unlimited."""
        from xruntime._runtime._middleware._quota import (
            QuotaConfig,
            QuotaTracker,
        )

        tracker = QuotaTracker(QuotaConfig())
        tracker.consume_cost(999.0)
        assert tracker.cost_usd == 999.0
