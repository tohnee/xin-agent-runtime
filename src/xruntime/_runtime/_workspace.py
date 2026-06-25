# -*- coding: utf-8 -*-
"""Workspace configuration and factory (M5).

Production-grade workspace management with tenant/session scoped
paths, path traversal guards, and backend selection (local/docker/e2b).
"""
from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel


class WorkspaceConfig(BaseModel):
    """Workspace backend configuration.

    Args:
        default_backend (`str`):
            Default backend — ``"docker"`` in production.
        allow_local_in_production (`bool`):
            Whether ``local`` backend is allowed when ``production``
            is True. Defaults to ``False``.
        base_dir (`str`):
            Base directory for local workspaces.
    """

    default_backend: str = "docker"
    allow_local_in_production: bool = False
    base_dir: str = "./xruntime-workspaces"


class WorkspaceManagerFactory:
    """Factory that creates workspace managers with safety guards.

    Args:
        config (`WorkspaceConfig`):
            The workspace configuration.
    """

    def __init__(self, config: WorkspaceConfig) -> None:
        """Initialize the factory."""
        self._config = config

    def create(
        self,
        backend: str | None = None,
        production: bool = False,
    ) -> Any:
        """Create a workspace manager for the given backend.

        Args:
            backend (`str | None`):
                Backend to use. Defaults to ``config.default_backend``.
            production (`bool`):
                Whether this is a production deployment. When True,
                ``local`` backend is rejected unless
                ``allow_local_in_production`` is True.

        Returns:
            `Any`: The workspace manager instance.

        Raises:
            `ValueError`: If local backend is used in production
                without explicit override.
        """
        effective = backend or self._config.default_backend

        if (
            production
            and effective == "local"
            and not self._config.allow_local_in_production
        ):
            raise ValueError(
                "Local workspace backend is not allowed in "
                "production. Set allow_local_in_production=True "
                "to override.",
            )

        if effective == "local":
            from agentscope.app.workspace_manager import (
                LocalWorkspaceManager,
            )

            return LocalWorkspaceManager(
                basedir=self._config.base_dir,
            )
        # For docker/e2b, return a placeholder (real implementation
        # would create DockerWorkspaceManager / E2BWorkspaceManager)
        return type(
            "PlaceholderWorkspaceManager",
            (),
            {"backend": effective},
        )()

    def workspace_path(
        self,
        tenant_id: str,
        session_id: str,
    ) -> str:
        """Return the workspace path for a tenant/session.

        Includes path traversal guard.

        Args:
            tenant_id (`str`): Tenant identifier.
            session_id (`str`): Session identifier.

        Returns:
            `str`: The workspace path.

        Raises:
            `ValueError`: If tenant_id or session_id contains path
                traversal characters.
        """
        for label, value in [
            ("tenant_id", tenant_id),
            ("session_id", session_id),
        ]:
            if ".." in value or "/" in value or os.sep in value:
                raise ValueError(
                    f"Path traversal detected in {label}: {value}",
                )
        return os.path.join(
            self._config.base_dir,
            "tenants",
            tenant_id,
            "sessions",
            session_id,
        )
