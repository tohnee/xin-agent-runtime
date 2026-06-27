# -*- coding: utf-8 -*-
"""Tests for SkillRegistry."""
from __future__ import annotations

import pytest

from xruntime._runtime._skills import (
    SkillContent,
    SkillManifest,
    SkillNotFoundError,
    SkillRegistry,
)


class TestSkillManifest:
    """Manifest model tests."""

    def test_defaults(self) -> None:
        m = SkillManifest(
            name="test",
            description="A test skill",
        )
        assert m.name == "test"
        assert m.version == "1.0.0"
        assert m.allowed_tools == []
        assert m.permissions == []
        assert m.instructions == ""

    def test_full(self) -> None:
        m = SkillManifest(
            name="coding",
            description="Write code",
            version="2.0.0",
            allowed_tools=["bash", "read_file"],
            permissions=["filesystem:read_write"],
            instructions="# Coding\nDo stuff",
        )
        assert m.version == "2.0.0"
        assert "bash" in m.allowed_tools
        assert "filesystem:read_write" in m.permissions


class TestSkillContent:
    """Content model tests."""

    def test_basic(self) -> None:
        c = SkillContent(
            name="test",
            instructions="Do things",
        )
        assert c.name == "test"
        assert c.instructions == "Do things"
        assert c.system_prompt_addition == ""

    def test_with_addition(self) -> None:
        c = SkillContent(
            name="test",
            instructions="Do things",
            system_prompt_addition="## Active Skill",
        )
        assert "Active Skill" in c.system_prompt_addition


class TestSkillRegistry:
    """Registry behaviour tests."""

    @pytest.fixture
    def registry(self, tmp_path) -> SkillRegistry:
        """Create a registry with test skills."""
        # Skill 1
        d1 = tmp_path / "skills" / "research"
        d1.mkdir(parents=True)
        (d1 / "SKILL.yaml").write_text(
            "name: research\n"
            "description: Conduct research\n"
            "version: '1.0.0'\n"
            "allowed_tools: [web_search, browse]\n"
            "permissions: [network]\n"
            "instructions: '# Research'\n"
        )
        # Skill 2
        d2 = tmp_path / "skills" / "coding"
        d2.mkdir(parents=True)
        (d2 / "SKILL.yaml").write_text(
            "name: coding\n"
            "description: Write code\n"
            "instructions: '# Coding'\n"
        )
        # Non-skill directory (no SKILL.yaml)
        d3 = tmp_path / "skills" / "not-a-skill"
        d3.mkdir(parents=True)

        return SkillRegistry(skill_dirs=[str(tmp_path / "skills")])

    def test_discover_finds_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        """discover() finds all SKILL.yaml files."""
        manifests = registry.discover()
        names = [m.name for m in manifests]
        assert "research" in names
        assert "coding" in names
        assert len(manifests) == 2

    def test_discover_empty_dir(self, tmp_path) -> None:
        """Empty directory returns empty list."""
        empty = tmp_path / "empty"
        empty.mkdir()
        reg = SkillRegistry(skill_dirs=[str(empty)])
        assert reg.discover() == []

    def test_discover_nonexistent_dir(self) -> None:
        """Non-existent directory is skipped."""
        reg = SkillRegistry(
            skill_dirs=["/nonexistent/path"],
        )
        assert reg.discover() == []

    def test_get_manifest_existing(
        self,
        registry: SkillRegistry,
    ) -> None:
        """get_manifest returns the manifest for a known skill."""
        m = registry.get_manifest("research")
        assert m is not None
        assert m.name == "research"
        assert "web_search" in m.allowed_tools

    def test_get_manifest_nonexistent(
        self,
        registry: SkillRegistry,
    ) -> None:
        """get_manifest returns None for unknown skill."""
        assert registry.get_manifest("unknown") is None

    def test_load_content_full(
        self,
        registry: SkillRegistry,
    ) -> None:
        """load_content returns full instructions."""
        content = registry.load_content("research")
        assert content.name == "research"
        assert "# Research" in content.instructions
        assert "Active Skill" in content.system_prompt_addition

    def test_load_content_nonexistent(
        self,
        registry: SkillRegistry,
    ) -> None:
        """load_content raises for unknown skill."""
        with pytest.raises(SkillNotFoundError):
            registry.load_content("nonexistent")

    def test_inject_to_system_prompt(
        self,
        registry: SkillRegistry,
    ) -> None:
        """inject_to_system_prompt produces formatted list."""
        text = registry.inject_to_system_prompt()
        assert "Available Skills" in text
        assert "research" in text
        assert "coding" in text
        assert "load_skill" in text

    def test_inject_empty_registry(self) -> None:
        """Empty registry produces empty string."""
        reg = SkillRegistry(skill_dirs=[])
        assert reg.inject_to_system_prompt() == ""

    def test_inject_specific_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        """inject_to_system_prompt with specific names."""
        text = registry.inject_to_system_prompt(
            skills=["coding"],
        )
        assert "coding" in text
        assert "research" not in text

    def test_skill_names(self, registry: SkillRegistry) -> None:
        """skill_names property returns all names."""
        names = registry.skill_names
        assert "research" in names
        assert "coding" in names

    def test_add_dir(self, tmp_path) -> None:
        """add_dir adds a new scan directory."""
        reg = SkillRegistry(skill_dirs=[])
        assert reg.discover() == []

        d = tmp_path / "extra" / "test-skill"
        d.mkdir(parents=True)
        (d / "SKILL.yaml").write_text("name: test-skill\ndescription: Test\n")
        reg.add_dir(str(tmp_path / "extra"))
        manifests = reg.discover()
        assert len(manifests) == 1
        assert manifests[0].name == "test-skill"

    def test_load_content_cached(
        self,
        registry: SkillRegistry,
    ) -> None:
        """load_content caches content on second call."""
        c1 = registry.load_content("research")
        c2 = registry.load_content("research")
        assert c1 is c2  # Same object (cached)
