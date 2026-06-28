# -*- coding: utf-8 -*-
"""TaskTool — main agent tool for delegating sub-tasks.

Inherits ToolBase for automatic toolkit registration.
"""
from __future__ import annotations

from typing import Any

from agentscope.permission import (
    PermissionBehavior,
    PermissionDecision,
)
from agentscope.tool import ToolBase

from ._executor import SubAgentExecutor
from ._models import SubAgentTask


class TaskTool(ToolBase):
    """Tool that lets the main agent delegate to sub-agents.

    Args:
        executor (`SubAgentExecutor`):
            The sub-agent executor to dispatch tasks through.
        default_runner (`Any | None`):
            Optional default runner function.
    """

    name: str = "task"
    description: str = (
        "Delegate a task to a sub-agent. "
        "Specify the sub-agent name and a clear "
        "task description. The sub-agent will execute "
        "in isolation and return a summary."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "subagent": {
                "type": "string",
                "description": "Name of the sub-agent to use.",
            },
            "description": {
                "type": "string",
                "description": "Clear description of the task.",
            },
        },
        "required": ["subagent", "description"],
    }
    is_read_only: bool = False
    is_concurrency_safe: bool = True

    def __init__(
        self,
        executor: SubAgentExecutor,
        default_runner: Any | None = None,
    ) -> None:
        """Initialize the tool.

        Args:
            executor: The sub-agent executor.
            default_runner: Optional default runner to use
                when no runner is passed to execute().
        """
        self._executor = executor
        self._default_runner = default_runner
        super().__init__()

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        """Allow task delegation (controlled by RBAC middleware)."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Task delegation allowed.",
        )

    async def __call__(
        self,
        subagent: str,
        description: str,
    ) -> dict[str, Any]:
        """Delegate a task to a sub-agent.

        Args:
            subagent (`str`): Name of the sub-agent to use.
            description (`str`): Clear description of the task.

        Returns:
            `dict`: Result with ``summary``, ``success``, and
            optionally ``findings`` or ``errors``.
        """
        task = SubAgentTask(
            spec_name=subagent,
            objective=description,
        )
        result = await self._executor.execute(
            task,
            runner=self._default_runner,
        )
        response: dict[str, Any] = {
            "summary": result.summary,
            "success": result.success,
        }
        if result.findings:
            response["findings"] = result.findings
        if result.errors:
            response["errors"] = result.errors
        return response
