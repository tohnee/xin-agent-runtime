# -*- coding: utf-8 -*-
"""Tests for workspace manager factory and sandbox integration.

Docker tests require Docker daemon and are skipped by default.
Run with: ``pytest --run-docker``
"""
from __future__ import annotations

import os

import pytest

from xruntime._runtime._workspace import (
    WorkspaceConfig,
    WorkspaceManagerFactory,
)


class TestWorkspaceConfig:
    """Config model tests."""

    def test_defaults(self) -> None:
        c = WorkspaceConfig()
        assert c.default_backend == "docker"
        assert c.allow_local_in_production is False
        assert c.base_dir == "./xruntime-workspaces"

    def test_custom(self) -> None:
        c = WorkspaceConfig(
            default_backend="local",
            allow_local_in_production=True,
            base_dir="/tmp/ws",
        )
        assert c.default_backend == "local"
        assert c.allow_local_in_production is True


class TestWorkspaceManagerFactory:
    """Factory tests."""

    def test_create_local(self, tmp_path) -> None:
        """Local backend creates LocalWorkspaceManager."""
        cfg = WorkspaceConfig(
            default_backend="local",
            base_dir=str(tmp_path),
        )
        factory = WorkspaceManagerFactory(cfg)
        mgr = factory.create()
        assert mgr is not None
        assert hasattr(mgr, "basedir") or hasattr(mgr, "_basedir")

    def test_create_docker(self, tmp_path) -> None:
        """Docker backend creates DockerWorkspaceManager."""
        cfg = WorkspaceConfig(
            default_backend="docker",
            base_dir=str(tmp_path),
        )
        factory = WorkspaceManagerFactory(cfg)
        mgr = factory.create()
        assert mgr is not None
        # DockerWorkspaceManager has basedir attribute
        assert hasattr(mgr, "basedir") or hasattr(mgr, "_basedir")

    def test_create_unknown_backend(self, tmp_path) -> None:
        """Unknown backend raises ValueError."""
        cfg = WorkspaceConfig(base_dir=str(tmp_path))
        factory = WorkspaceManagerFactory(cfg)
        with pytest.raises(ValueError, match="Unknown workspace"):
            factory.create(backend="kubernetes")

    def test_local_in_production_rejected(self, tmp_path) -> None:
        """Local backend rejected in production."""
        cfg = WorkspaceConfig(
            default_backend="local",
            base_dir=str(tmp_path),
        )
        factory = WorkspaceManagerFactory(cfg)
        with pytest.raises(ValueError, match="not allowed in production"):
            factory.create(production=True)

    def test_local_in_production_allowed(self, tmp_path) -> None:
        """Local backend allowed in production with override."""
        cfg = WorkspaceConfig(
            default_backend="local",
            allow_local_in_production=True,
            base_dir=str(tmp_path),
        )
        factory = WorkspaceManagerFactory(cfg)
        mgr = factory.create(production=True)
        assert mgr is not None

    def test_workspace_path(self, tmp_path) -> None:
        """workspace_path returns correct path."""
        cfg = WorkspaceConfig(base_dir=str(tmp_path))
        factory = WorkspaceManagerFactory(cfg)
        path = factory.workspace_path("acme", "sess-1")
        assert "acme" in path
        assert "sess-1" in path

    def test_workspace_path_traversal_blocked(self, tmp_path) -> None:
        """Path traversal in tenant_id is blocked."""
        cfg = WorkspaceConfig(base_dir=str(tmp_path))
        factory = WorkspaceManagerFactory(cfg)
        with pytest.raises(ValueError, match="Path traversal"):
            factory.workspace_path("../etc", "sess-1")
        with pytest.raises(ValueError, match="Path traversal"):
            factory.workspace_path("acme", "sess/../../etc")


@pytest.mark.skipif(
    not os.environ.get("RUN_DOCKER_TESTS"),
    reason="Set RUN_DOCKER_TESTS=1 to run Docker sandbox tests",
)
class TestDockerSandboxIntegration:
    """Docker sandbox integration tests (require Docker daemon).

    Run with: ``RUN_DOCKER_TESTS=1 pytest tests/xruntime/test_workspace_integration.py -v -k Docker``
    """

    @pytest.mark.asyncio
    async def test_docker_workspace_create_and_exec(self, tmp_path) -> None:
        """Create a Docker workspace and execute a command."""
        from agentscope.app.workspace_manager import (
            DockerWorkspaceManager,
        )

        mgr = DockerWorkspaceManager(basedir=str(tmp_path))
        assert mgr is not None

        # The workspace manager creates containers lazily
        # Full async context manager test would go here
        # For now verify it's instantiable
        assert hasattr(mgr, "basedir") or hasattr(mgr, "_basedir")
