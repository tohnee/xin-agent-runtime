# -*- coding: utf-8 -*-
"""LoadSkillTool — Agent tool for on-demand skill loading.

Simple callable wrapper (does not inherit ToolBase to avoid
abstract-method coupling). Can be registered in a Toolkit
by wrapping the __call__ method.
"""
from __future__ import annotations

from typing import Any

from ._registry import SkillNotFoundError, SkillRegistry


class LoadSkillTool:
    """Tool that lets an Agent load a skill's full instructions.

    Args:
        registry (`SkillRegistry`):
            The skill registry to load from.
    """

    def __init__(self, registry: SkillRegistry) -> None:
        """Initialize the tool."""
        self._registry = registry
        self.name = "load_skill"
        self.description = (
            "Load the full instructions for a skill by name. "
            "Use this after reading the Available Skills list "
            "in the system prompt to get detailed guidance."
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
