# -*- coding: utf-8 -*-
"""Tests for P0: WorkspaceConfig wired into _server.py."""
import os

import pytest


class TestServerWorkspaceWiring:
    """_server.py uses WorkspaceManagerFactory, not hardcoded LocalWorkspaceManager."""

    def test_server_imports_workspace_factory(self) -> None:
        """_server.py imports WorkspaceConfig and WorkspaceManagerFactory."""
        import inspect

        from xruntime import _server

        src = inspect.getsource(_server)
        assert "WorkspaceManagerFactory" in src
        assert "WorkspaceConfig" in src

    def test_server_reads_workspace_backend_env(self) -> None:
        """_server.py reads XRUNTIME_WORKSPACE_BACKEND env."""
        import inspect

        from xruntime import _server

        src = inspect.getsource(_server)
        assert "XRUNTIME_WORKSPACE_BACKEND" in src

    def test_server_reads_production_env(self) -> None:
        """_server.py reads XRUNTIME_PRODUCTION env."""
        import inspect

        from xruntime import _server

        src = inspect.getsource(_server)
        assert "XRUNTIME_PRODUCTION" in src

    def test_production_rejects_local_workspace(self) -> None:
        """Setting XRUNTIME_PRODUCTION=1 with local backend raises."""
        from xruntime._runtime._workspace import (
            WorkspaceConfig,
            WorkspaceManagerFactory,
        )

        config = WorkspaceConfig(
            default_backend="local",
            allow_local_in_production=False,
        )
        factory = WorkspaceManagerFactory(config)
        with pytest.raises(ValueError, match="local.*production"):
            factory.create(backend="local", production=True)

    def test_production_allows_docker(self) -> None:
        """Production with docker backend succeeds."""
        from xruntime._runtime._workspace import (
            WorkspaceConfig,
            WorkspaceManagerFactory,
        )

        config = WorkspaceConfig(default_backend="docker")
        factory = WorkspaceManagerFactory(config)
        manager = factory.create(backend="docker", production=True)
        assert manager is not None

    def test_non_production_allows_local(self) -> None:
        """Non-production allows local workspace."""
        from xruntime._runtime._workspace import (
            WorkspaceConfig,
            WorkspaceManagerFactory,
        )

        config = WorkspaceConfig(default_backend="local")
        factory = WorkspaceManagerFactory(config)
        manager = factory.create(backend="local", production=False)
        assert manager is not None
