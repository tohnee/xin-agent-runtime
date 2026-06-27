# -*- coding: utf-8 -*-
"""SkillRegistry — discovery, parsing, and caching of skills."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ._manifest import SkillContent, SkillManifest


class SkillNotFoundError(Exception):
    """Raised when a requested skill is not found."""

    def __init__(self, name: str) -> None:
        """Initialize error."""
        self.skill_name = name
        super().__init__(f"Skill not found: {name}")


class SkillRegistry:
    """Skill registration and discovery center.

    Scans configured directories for ``SKILL.yaml`` files, parses
    them into :class:`SkillManifest` objects, and provides on-demand
    loading of full skill content.

    Args:
        skill_dirs (`list[str]`):
            Directories to scan for skills. Each subdirectory
            containing a ``SKILL.yaml`` is treated as a skill.
    """

    def __init__(
        self,
        skill_dirs: list[str] | None = None,
    ) -> None:
        """Initialize the registry."""
        self._skill_dirs = [Path(d) for d in (skill_dirs or [])]
        self._manifests: dict[str, SkillManifest] = {}
        self._contents: dict[str, SkillContent] = {}
        self._discovered = False

    def add_dir(self, dir_path: str) -> None:
        """Add a skill directory to scan.

        Args:
            dir_path (`str`): Path to a directory containing skills.
        """
        p = Path(dir_path)
        if p not in self._skill_dirs:
            self._skill_dirs.append(p)
        self._discovered = False

    def discover(self) -> list[SkillManifest]:
        """Scan all directories and parse SKILL.yaml files.

        Returns:
            `list[SkillManifest]`: All discovered skill manifests.
        """
        self._manifests.clear()
        self._contents.clear()

        for skill_dir in self._skill_dirs:
            if not skill_dir.exists():
                continue
            for entry in sorted(skill_dir.iterdir()):
                if not entry.is_dir():
                    continue
                yaml_path = entry / "SKILL.yaml"
                if not yaml_path.exists():
                    continue
                manifest = self._parse_yaml(yaml_path)
                if manifest:
                    self._manifests[manifest.name] = manifest

        self._discovered = True
        return list(self._manifests.values())

    def get_manifest(
        self,
        name: str,
    ) -> SkillManifest | None:
        """Get a skill manifest by name.

        Args:
            name (`str`): Skill name.

        Returns:
            `SkillManifest | None`: The manifest, or None if not found.
        """
        if not self._discovered:
            self.discover()
        return self._manifests.get(name)

    def load_content(self, name: str) -> SkillContent:
        """Load full skill content by name.

        Args:
            name (`str`): Skill name.

        Returns:
            `SkillContent`: The full skill content.

        Raises:
            `SkillNotFoundError`: If the skill does not exist.
        """
        if not self._discovered:
            self.discover()

        if name in self._contents:
            return self._contents[name]

        manifest = self._manifests.get(name)
        if manifest is None:
            raise SkillNotFoundError(name)

        content = SkillContent(
            name=name,
            instructions=manifest.instructions,
            system_prompt_addition=(
                f"\n\n## Active Skill: {name}\n" f"{manifest.instructions}"
            ),
        )
        self._contents[name] = content
        return content

    def inject_to_system_prompt(
        self,
        skills: list[str] | None = None,
    ) -> str:
        """Format skill list for system prompt injection.

        Produces a concise list of available skills that the
        Agent can read to decide which skill to load.

        Args:
            skills (`list[str] | None`):
                Specific skill names to include. If None,
                all discovered skills are listed.

        Returns:
            `str`: Formatted skill list text.
        """
        if not self._discovered:
            self.discover()

        if skills is None:
            manifests = list(self._manifests.values())
        else:
            manifests = [
                self._manifests[n] for n in skills if n in self._manifests
            ]

        if not manifests:
            return ""

        lines = ["", "## Available Skills", ""]
        for i, m in enumerate(manifests, 1):
            desc = m.description.strip().split("\n")[0]
            lines.append(f"{i}. **{m.name}**: {desc}")
        lines.append("")
        lines.append(
            "Use the `load_skill` tool to load a skill's "
            "full instructions when needed."
        )
        return "\n".join(lines)

    @property
    def skill_names(self) -> list[str]:
        """Names of all discovered skills."""
        if not self._discovered:
            self.discover()
        return list(self._manifests.keys())

    @staticmethod
    def _parse_yaml(
        path: Path,
    ) -> SkillManifest | None:
        """Parse a SKILL.yaml file into a SkillManifest.

        Args:
            path (`Path`): Path to the SKILL.yaml file.

        Returns:
            `SkillManifest | None`: Parsed manifest, or None on error.
        """
        try:
            with open(path, encoding="utf-8") as f:
                data: dict[str, Any] = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            return None

        if not data or "name" not in data:
            return None

        return SkillManifest(
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            allowed_tools=data.get("allowed_tools", []),
            permissions=data.get("permissions", []),
            instructions=data.get("instructions", ""),
        )
