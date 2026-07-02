# -*- coding: utf-8 -*-
"""SubAgent system models — spec, task, result."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    """UTC now."""
    return datetime.now(timezone.utc)


class SubAgentSpec(BaseModel):
    """Specification for a sub-agent type.

    Args:
        name (`str`): Unique identifier (kebab-case).
        description (`str`): When to delegate to this sub-agent.
        system_prompt (`str`): System prompt for the sub-agent.
        model_config_name (`str`): Model config to use.
        allowed_tools (`list[str]`): Tools the sub-agent may use.
            Empty list means all tools.
        max_turns (`int`): Maximum conversation turns.
    """

    name: str
    description: str
    system_prompt: str = "You are a helpful assistant."
    model_config_name: str = ""
    allowed_tools: list[str] = []
    max_turns: int = 10


class SubAgentTask(BaseModel):
    """A task package for a sub-agent — context-isolated.

    Args:
        task_id (`str`): Unique task identifier.
        parent_session_id (`str`): Parent session for correlation.
        spec_name (`str`): Name of the SubAgentSpec to use.
        objective (`str`): The task objective.
        constraints (`list[str]`): Task constraints.
        input_context (`str`): Necessary context summary.
        expected_output (`str`): Expected output format.
    """

    task_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_session_id: str = ""
    spec_name: str
    objective: str
    constraints: list[str] = []
    input_context: str = ""
    expected_output: str = ""


class SubAgentResult(BaseModel):
    """Result of a sub-agent task execution.

    Args:
        task_id (`str`): The task that produced this result.
        success (`bool`): Whether execution succeeded.
        summary (`str`): Result summary.
        findings (`list[str]`): Detailed findings.
        artifacts (`list[str]`): Artifact file paths.
        errors (`list[str]`): Error messages.
        token_usage (`int`): Total tokens consumed.
        duration_seconds (`float`): Execution duration.
    """

    task_id: str
    success: bool = True
    summary: str = ""
    findings: list[str] = []
    artifacts: list[str] = []
    errors: list[str] = []
    token_usage: int = 0
    duration_seconds: float = 0.0
