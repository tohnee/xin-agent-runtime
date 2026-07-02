# -*- coding: utf-8 -*-
"""P4-B: CanaryDeployment — canary release for workflows."""
from __future__ import annotations

import random

from ._registry import WorkflowRegistry


class CanaryDeployment:
    """Canary deployment for gradual workflow rollout.

    Args:
        registry (`WorkflowRegistry`): The workflow registry.
        workflow_id (`str`): The workflow id.
        canary_version (`str`): The new version label.
        stable_version (`str`): The stable version label.
        canary_percent (`float`): Percentage (0-100) of
            traffic to route to canary.
    """

    def __init__(
        self,
        registry: WorkflowRegistry,
        workflow_id: str,
        canary_version: str = "v2",
        stable_version: str = "v1",
        canary_percent: float = 0.0,
    ) -> None:
        self._registry = registry
        self._workflow_id = workflow_id
        self._canary_version = canary_version
        self._stable_version = stable_version
        self._canary_percent = max(0.0, min(100.0, canary_percent))

    @property
    def canary_percent(self) -> float:
        """Return the canary percentage."""
        return self._canary_percent

    def set_canary_percent(self, percent: float) -> None:
        """Update the canary percentage (0-100)."""
        self._canary_percent = max(0.0, min(100.0, percent))

    def select_version(self) -> str:
        """Select a version based on canary percentage.

        Returns:
            `str`: The selected version label.
        """
        if self._canary_percent <= 0:
            return self._stable_version
        if self._canary_percent >= 100:
            return self._canary_version
        if random.random() * 100 < self._canary_percent:
            return self._canary_version
        return self._stable_version

    def promote_to_stable(self) -> bool:
        """Promote canary to stable (100% + set as default).

        Returns:
            `bool`: True if promoted.
        """
        ok = self._registry.set_default(
            self._workflow_id,
            self._canary_version,
        )
        if ok:
            self._stable_version = self._canary_version
            self._canary_percent = 0.0
        return ok

    def rollback(self) -> None:
        """Rollback: 0% canary, revert to stable."""
        self._canary_percent = 0.0
        self._registry.set_default(
            self._workflow_id,
            self._stable_version,
        )


__all__ = ["CanaryDeployment"]
