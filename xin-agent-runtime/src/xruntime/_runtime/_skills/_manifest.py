# -*- coding: utf-8 -*-
"""Skill system models — manifest and content definitions."""
from __future__ import annotations

from pydantic import BaseModel


class SkillManifest(BaseModel):
    """Skill metadata — Stage 1, always loaded (~100 tokens/skill).

    Args:
        name (`str`): Unique kebab-case identifier.
        description (`str`): When to use this skill.
        version (`str`): Semantic version.
        allowed_tools (`list[str]`): Tools the skill may use.
            Empty list means all tools.
        permissions (`list[str]`): Required permissions.
        instructions (`str`): Full skill instructions (Stage 2).
            Empty string means instructions not loaded yet.
    """

    name: str
    description: str
    version: str = "1.0.0"
    allowed_tools: list[str] = []
    permissions: list[str] = []
    instructions: str = ""


class SkillContent(BaseModel):
    """Skill full content — Stage 2, loaded on demand.

    Args:
        name (`str`): Skill name.
        instructions (`str`): Markdown instructions for the Agent.
        system_prompt_addition (`str`): Text to append to
            the system prompt when this skill is active.
    """

    name: str
    instructions: str
    system_prompt_addition: str = ""
