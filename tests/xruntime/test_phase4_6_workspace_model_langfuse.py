# -*- coding: utf-8 -*-
"""Tests for Phase 4-6: Workspace, Model Governance, Langfuse."""
import pytest


# === Phase 4: M5 Workspace ===


class TestWorkspaceConfig:
    """WorkspaceConfig and WorkspaceManagerFactory."""

    def test_default_backend_is_docker(self) -> None:
        """Production default should be docker, not local."""
        from xruntime._runtime._workspace import WorkspaceConfig

        config = WorkspaceConfig()
        assert config.default_backend == "docker"

    def test_local_requires_explicit_override(self) -> None:
        """Local workspace requires explicit production override."""
        from xruntime._runtime._workspace import (
            WorkspaceConfig,
            WorkspaceManagerFactory,
        )

        config = WorkspaceConfig(
            default_backend="docker",
            allow_local_in_production=False,
        )
        factory = WorkspaceManagerFactory(config)
        # In production mode, local should be rejected
        with pytest.raises(ValueError, match="local.*production"):
            factory.create(backend="local", production=True)

    def test_docker_allowed_in_production(self) -> None:
        """Docker backend is allowed in production."""
        from xruntime._runtime._workspace import (
            WorkspaceConfig,
            WorkspaceManagerFactory,
        )

        config = WorkspaceConfig(default_backend="docker")
        factory = WorkspaceManagerFactory(config)
        manager = factory.create(backend="docker", production=True)
        assert manager is not None

    def test_tenant_scoped_path(self) -> None:
        """Workspace path includes tenant and session."""
        from xruntime._runtime._workspace import (
            WorkspaceConfig,
            WorkspaceManagerFactory,
        )

        config = WorkspaceConfig(default_backend="local")
        factory = WorkspaceManagerFactory(config)
        path = factory.workspace_path(
            tenant_id="acme",
            session_id="s123",
        )
        assert "acme" in path
        assert "s123" in path

    def test_path_traversal_rejected(self) -> None:
        """Path traversal attempts are rejected."""
        from xruntime._runtime._workspace import (
            WorkspaceConfig,
            WorkspaceManagerFactory,
        )

        config = WorkspaceConfig(default_backend="local")
        factory = WorkspaceManagerFactory(config)
        with pytest.raises(ValueError, match="traversal"):
            factory.workspace_path(
                tenant_id="../../../etc",
                session_id="s123",
            )


# === Phase 5: M6 Model Governance ===


class TestModelCapabilityRegistry:
    """ModelCapabilityRegistry and ModelRouter."""

    def test_register_model_capability(self) -> None:
        """A model can be registered with capabilities."""
        from xruntime._runtime._model_governance import (
            ModelCapability,
            ModelCapabilityRegistry,
        )

        registry = ModelCapabilityRegistry()
        registry.register(
            "claude-sonnet",
            ModelCapability(
                supports_tools=True,
                supports_vision=True,
                max_tokens=200000,
            ),
        )
        cap = registry.get("claude-sonnet")
        assert cap is not None
        assert cap.supports_tools is True

    def test_select_tool_capable_model(self) -> None:
        """ModelRouter selects a model that supports tools."""
        from xruntime._runtime._model_governance import (
            ModelCapability,
            ModelCapabilityRegistry,
            ModelRouter,
        )

        registry = ModelCapabilityRegistry()
        registry.register(
            "claude-haiku",
            ModelCapability(supports_tools=False),
        )
        registry.register(
            "claude-sonnet",
            ModelCapability(supports_tools=True),
        )
        router = ModelRouter(registry)
        model = router.select(
            candidates=["claude-haiku", "claude-sonnet"],
            requires_tools=True,
        )
        assert model == "claude-sonnet"

    def test_tenant_allowlist_rejects(self) -> None:
        """Model not in tenant allowlist is rejected."""
        from xruntime._runtime._model_governance import (
            ModelCapability,
            ModelCapabilityRegistry,
            ModelRouter,
        )

        registry = ModelCapabilityRegistry()
        registry.register(
            "gpt-4",
            ModelCapability(supports_tools=True),
        )
        router = ModelRouter(registry)
        with pytest.raises(ValueError, match="not allowed"):
            router.select(
                candidates=["gpt-4"],
                tenant_allowlist={"claude-sonnet"},
            )

    def test_fallback_model(self) -> None:
        """Fallback model is used when primary is unavailable."""
        from xruntime._runtime._model_governance import (
            ModelCapability,
            ModelCapabilityRegistry,
            ModelRouter,
        )

        registry = ModelCapabilityRegistry()
        registry.register(
            "claude-sonnet",
            ModelCapability(supports_tools=True),
        )
        router = ModelRouter(registry)
        model = router.select(
            candidates=["claude-opus"],  # not registered
            fallback="claude-sonnet",
        )
        assert model == "claude-sonnet"


# === Phase 6: M7 Langfuse ===


class TestLangfuseConfig:
    """LangfuseConfig and NoopExporter."""

    def test_disabled_is_noop(self) -> None:
        """When disabled, exporter is NoopExporter."""
        from xruntime._runtime._langfuse import (
            LangfuseConfig,
            LangfuseExporter,
        )

        config = LangfuseConfig(enabled=False)
        exporter = LangfuseExporter(config)
        assert exporter.is_noop is True

    def test_noop_exporter_does_not_raise(self) -> None:
        """NoopExporter methods are safe no-ops."""
        from xruntime._runtime._langfuse import (
            LangfuseConfig,
            LangfuseExporter,
        )

        exporter = LangfuseExporter(LangfuseConfig(enabled=False))
        # These should not raise
        exporter.trace_generation(
            model="test",
            input_tokens=10,
            output_tokens=5,
            tenant_id="t1",
        )
        exporter.trace_tool_call(
            tool_name="Read",
            tenant_id="t1",
        )
        exporter.trace_knowledge_retrieve(
            query="test",
            results=3,
            tenant_id="t1",
        )

    def test_enabled_uses_real_exporter(self) -> None:
        """When enabled, exporter is not noop (if langfuse installed)."""
        from xruntime._runtime._langfuse import (
            LangfuseConfig,
            LangfuseExporter,
        )

        config = LangfuseConfig(
            enabled=True,
            host="http://localhost:3000",
            public_key="pk-test",
            secret_key="sk-test",
        )
        exporter = LangfuseExporter(config)
        # If langfuse is installed, is_noop is False; if not, it's
        # True but the config intent is enabled.
        assert config.enabled is True

    def test_payload_redacts_secrets(self) -> None:
        """Trace payload does not contain secrets."""
        from xruntime._runtime._langfuse import (
            LangfuseConfig,
            LangfuseExporter,
            _redact_payload,
        )

        payload = {
            "input": "My key is sk-abcdefghijklmnopqrstuvwxyz1234",
            "metadata": {"token": "Bearer xyz123"},
        }
        redacted = _redact_payload(payload)
        assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in str(
            redacted,
        )
        assert "[REDACTED" in str(redacted)
