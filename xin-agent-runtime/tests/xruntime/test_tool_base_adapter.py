# -*- coding: utf-8 -*-
"""Tests for ToolBase adapter compliance."""
from __future__ import annotations

import pytest

from agentscope.permission import PermissionBehavior
from agentscope.tool import ToolBase

from xruntime._runtime._skills import SkillRegistry
from xruntime._runtime._skills._load_skill_tool import LoadSkillTool
from xruntime._runtime._subagents import SubAgentExecutor
from xruntime._runtime._subagents._task_tool import TaskTool


class TestLoadSkillToolBaseAdapter:
    """LoadSkillTool inherits ToolBase."""

    def test_is_toolbase(self, tmp_path) -> None:
        """LoadSkillTool is a ToolBase instance."""
        d = tmp_path / "skills" / "research"
        d.mkdir(parents=True)
        (d / "SKILL.yaml").write_text(
            "name: research\ndescription: Do research\n",
        )
        registry = SkillRegistry(skill_dirs=[str(tmp_path / "skills")])
        registry.discover()
        tool = LoadSkillTool(registry)
        assert isinstance(tool, ToolBase)

    def test_has_name_and_description(self, tmp_path) -> None:
        """Tool has name and description."""
        registry = SkillRegistry(skill_dirs=[])
        tool = LoadSkillTool(registry)
        assert tool.name == "load_skill"
        assert len(tool.description) > 10

    def test_has_input_schema(self, tmp_path) -> None:
        """Tool has input_schema."""
        registry = SkillRegistry(skill_dirs=[])
        tool = LoadSkillTool(registry)
        assert "skill_name" in tool.input_schema["properties"]

    def test_check_permissions_allows(self, tmp_path) -> None:
        """check_permissions returns allow."""
        registry = SkillRegistry(skill_dirs=[])
        tool = LoadSkillTool(registry)

        async def check():
            return await tool.check_permissions({}, None)

        import asyncio

        decision = asyncio.run(check())
        assert decision.behavior == PermissionBehavior.ALLOW

    @pytest.mark.asyncio
    async def test_call_returns_content(self, tmp_path) -> None:
        """__call__ returns skill content."""
        d = tmp_path / "skills" / "research"
        d.mkdir(parents=True)
        (d / "SKILL.yaml").write_text(
            "name: research\ndescription: Do research\n",
        )
        registry = SkillRegistry(skill_dirs=[str(tmp_path / "skills")])
        registry.discover()
        tool = LoadSkillTool(registry)
        result = await tool(skill_name="research")
        assert "instructions" in result
        assert result["name"] == "research"

    @pytest.mark.asyncio
    async def test_call_nonexistent_returns_error(self) -> None:
        """__call__ on unknown skill returns error."""
        registry = SkillRegistry(skill_dirs=[])
        tool = LoadSkillTool(registry)
        result = await tool(skill_name="nonexistent")
        assert "error" in result


class TestTaskToolBaseAdapter:
    """TaskTool inherits ToolBase."""

    def test_is_toolbase(self) -> None:
        """TaskTool is a ToolBase instance."""
        executor = SubAgentExecutor(specs=[], max_concurrent=2)
        tool = TaskTool(executor)
        assert isinstance(tool, ToolBase)

    def test_has_name_and_description(self) -> None:
        """Tool has name and description."""
        executor = SubAgentExecutor(specs=[])
        tool = TaskTool(executor)
        assert tool.name == "task"
        assert len(tool.description) > 10

    def test_has_input_schema(self) -> None:
        """Tool has input_schema."""
        executor = SubAgentExecutor(specs=[])
        tool = TaskTool(executor)
        assert "subagent" in tool.input_schema["properties"]
        assert "description" in tool.input_schema["properties"]

    def test_check_permissions_allows(self) -> None:
        """check_permissions returns allow."""
        executor = SubAgentExecutor(specs=[])
        tool = TaskTool(executor)

        async def check():
            return await tool.check_permissions({}, None)

        import asyncio

        decision = asyncio.run(check())
        assert decision.behavior == PermissionBehavior.ALLOW
