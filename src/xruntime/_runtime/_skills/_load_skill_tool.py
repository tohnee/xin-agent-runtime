# -*- coding: utf-8 -*-
"""LoadSkillTool — Agent tool for on-demand skill loading.

Inherits ToolBase for automatic toolkit registration.
"""
from __future__ import annotations

from typing import Any

from agentscope.permission import (
    PermissionBehavior,
    PermissionDecision,
)
from agentscope.tool import ToolBase

from ._registry import SkillNotFoundError, SkillRegistry


class LoadSkillTool(ToolBase):
    """Tool that lets an Agent load a skill's full instructions.

    Args:
        registry (`SkillRegistry`):
            The skill registry to load from.
    """

    name: str = "load_skill"
    description: str = (
        "Load the full instructions for a skill by name. "
        "Use this after reading the Available Skills list "
        "in the system prompt to get detailed guidance."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "The name of the skill to load.",
            },
        },
        "required": ["skill_name"],
    }
    is_read_only: bool = True
    is_concurrency_safe: bool = True

    def __init__(self, registry: SkillRegistry) -> None:
        """Initialize the tool."""
        self._registry = registry
        super().__init__()

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        """Allow all skill loads (read-only operation)."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Skill loading is read-only.",
        )

    async def __call__(
        self,
        skill_name: str,
    ) -> dict[str, Any]:
        """Load a skill's full content.

        Args:
            skill_name (`str`): The name of the skill to load.

        Returns:
            `dict`: Skill content with ``instructions`` key.
            On error, returns a dict with ``error`` key.
        """
        try:
            content = self._registry.load_content(skill_name)
        except SkillNotFoundError:
            return {
                "error": f"Skill '{skill_name}' not found. "
                f"Available: {self._registry.skill_names}",
            }
        return {
            "name": content.name,
            "instructions": content.instructions,
        }
