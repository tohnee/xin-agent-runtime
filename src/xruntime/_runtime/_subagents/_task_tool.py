# -*- coding: utf-8 -*-
"""TaskTool — main agent tool for delegating sub-tasks."""
from __future__ import annotations

from typing import Any

from ._executor import SubAgentExecutor
from ._models import SubAgentTask


class TaskTool:
    """Tool that lets the main agent delegate to sub-agents.

    This is a callable wrapper around :class:`SubAgentExecutor`.
    It does not inherit ``ToolBase`` to avoid abstract-method
    coupling; it can be registered as a tool via ``Toolkit``
    by wrapping the ``__call__`` method.

    Args:
        executor (`SubAgentExecutor`):
            The sub-agent executor to dispatch tasks through.
    """

    def __init__(self, executor: SubAgentExecutor) -> None:
        """Initialize the tool."""
        self._executor = executor
        self.name = "task"
        self.description = (
            "Delegate a task to a sub-agent. "
            "Specify the sub-agent name and a clear "
            "task description. The sub-agent will execute "
            "in isolation and return a summary."
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
        result = await self._executor.execute(task)
        response: dict[str, Any] = {
            "summary": result.summary,
            "success": result.success,
        }
        if result.findings:
            response["findings"] = result.findings
        if result.errors:
            response["errors"] = result.errors
        return response
