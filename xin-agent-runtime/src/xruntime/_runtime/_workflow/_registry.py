# -*- coding: utf-8 -*-
"""P4-B: WorkflowRegistry — versioned workflow management."""
from __future__ import annotations

from typing import Any

from .._orchestrator import Workflow


class WorkflowRegistry:
    """Registry for versioned workflows.

    Args:
        workflows (`dict`): Pre-populated workflows (optional).
    """

    def __init__(self) -> None:
        self._versions: dict[str, dict[str, Workflow]] = {}
        self._defaults: dict[str, str] = {}

    def register(
        self,
        workflow: Workflow,
        version: str = "v1",
        default: bool = False,
    ) -> None:
        """Register a workflow version.

        Args:
            workflow (`Workflow`): The workflow.
            version (`str`): Version label.
            default (`bool`): Set as default version.
        """
        wf_id = workflow.id
        if wf_id not in self._versions:
            self._versions[wf_id] = {}
        self._versions[wf_id][version] = workflow
        if default or wf_id not in self._defaults:
            self._defaults[wf_id] = version

    def get(
        self,
        workflow_id: str,
        version: str | None = None,
    ) -> Workflow | None:
        """Get a workflow by id and optional version.

        Args:
            workflow_id (`str`): The workflow id.
            version (`str | None`): Version. ``None`` uses
                default.

        Returns:
            `Workflow | None`: The workflow, or ``None``.
        """
        versions = self._versions.get(workflow_id)
        if versions is None:
            return None
        if version is None:
            version = self._defaults.get(workflow_id, "")
        return versions.get(version)

    def get_default_version(
        self,
        workflow_id: str,
    ) -> str | None:
        """Return the default version label."""
        return self._defaults.get(workflow_id)

    def set_default(
        self,
        workflow_id: str,
        version: str,
    ) -> bool:
        """Set the default version.

        Returns:
            `bool`: True if set, False if version not found.
        """
        if (
            workflow_id not in self._versions
            or version not in self._versions[workflow_id]
        ):
            return False
        self._defaults[workflow_id] = version
        return True

    def list_versions(
        self,
        workflow_id: str,
    ) -> list[str]:
        """List all versions for a workflow."""
        return list(self._versions.get(workflow_id, {}).keys())

    @property
    def workflow_count(self) -> int:
        """Return number of registered workflows."""
        return len(self._versions)


__all__ = ["WorkflowRegistry"]
