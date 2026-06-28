# -*- coding: utf-8 -*-
"""SkillInjectionMiddleware — inject available skills into the
Agent's system prompt via the ``on_system_prompt`` transformer hook.

Stage 1: Always inject a concise skill list (~100 tokens/skill)
so the Agent knows what skills are available. The Agent can then
use the ``load_skill`` tool to load full instructions (Stage 2).
"""
from __future__ import annotations

from typing import Any

from agentscope.middleware import MiddlewareBase

from .._skills._registry import SkillRegistry

_SKILL_PROMPT_FOOTER = (
    "\n\nUse the `load_skill` tool to load a skill's "
    "full instructions when needed."
)


class SkillInjectionMiddleware(MiddlewareBase):
    """Inject the available skill list into the system prompt.

    Args:
        registry (`SkillRegistry`):
            The skill registry to read skills from.
    """

    def __init__(self, registry: SkillRegistry) -> None:
        """Initialize the middleware."""
        self._registry = registry

    async def on_system_prompt(
        self,
        agent: Any,
        current_prompt: str,
    ) -> str:
        """Append the skill list to the system prompt.

        Args:
            agent: The Agent instance.
            current_prompt: The current system prompt.

        Returns:
            The modified system prompt with skill list appended.
        """
        skill_text = self._registry.inject_to_system_prompt()
        if not skill_text:
            return current_prompt
        return current_prompt + skill_text + _SKILL_PROMPT_FOOTER
