# -*- coding: utf-8 -*-
"""OpenCode config JSON Schema validation.

Validates OpenCode SDK config fragments before they enter the
runtime. Also provides ``tighten_permissions`` to ensure client
permissions can only tighten (never widen) the tenant policy.
"""
from __future__ import annotations

from typing import Any


def validate_opencode_config(
    config: dict[str, Any],
) -> list[str]:
    """Validate an OpenCode config fragment.

    Args:
        config (`dict[str, Any]`):
            The parsed OpenCode config.

    Returns:
        `list[str]`: List of validation error messages. Empty
        means valid.
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        errors.append("config must be a dict")
        return errors

    # agents must be a list of dicts (if present)
    if "agents" in config:
        agents = config["agents"]
        if not isinstance(agents, list):
            errors.append("agents must be a list")
        else:
            for i, agent in enumerate(agents):
                if not isinstance(agent, dict):
                    errors.append(
                        f"agents[{i}] must be a dict",
                    )
                elif "name" not in agent:
                    errors.append(
                        f"agents[{i}] missing required 'name'",
                    )

    # permissions must have allow/deny as lists (if present)
    if "permissions" in config:
        perms = config["permissions"]
        if not isinstance(perms, dict):
            errors.append("permissions must be a dict")
        else:
            for key in ("allow", "deny"):
                if key in perms and not isinstance(
                    perms[key],
                    list,
                ):
                    errors.append(
                        f"permissions.{key} must be a list",
                    )

    # mcp must be a dict (if present)
    if "mcp" in config and not isinstance(config["mcp"], dict):
        errors.append("mcp must be a dict")

    # skills must be a list (if present)
    if "skills" in config and not isinstance(
        config["skills"],
        list,
    ):
        errors.append("skills must be a list")

    # plugins must be a list (if present)
    if "plugins" in config and not isinstance(
        config["plugins"],
        list,
    ):
        errors.append("plugins must be a list")

    return errors


def tighten_permissions(
    client_perms: dict[str, Any],
    tenant_allowlist: set[str],
) -> dict[str, list[str]]:
    """Tighten client permissions to never exceed tenant policy.

    The client's ``allow`` list is intersected with the tenant
    allowlist. The client's ``deny`` list is always preserved
    (denials can only grow, never shrink).

    Args:
        client_perms (`dict[str, Any]`):
            Client-supplied permissions with ``allow`` and ``deny``
            lists.
        tenant_allowlist (`set[str]`):
            Tools the tenant is allowed to use.

    Returns:
        `dict[str, list[str]]`: Tightened permissions.
    """
    client_allow = client_perms.get("allow", [])
    client_deny = client_perms.get("deny", [])

    # Intersect allow with tenant allowlist
    tightened_allow = [t for t in client_allow if t in tenant_allowlist]

    # Deny list is preserved as-is (can only tighten)
    return {
        "allow": tightened_allow,
        "deny": list(client_deny),
    }
