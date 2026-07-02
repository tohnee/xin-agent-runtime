# -*- coding: utf-8 -*-
"""ScopeHierarchy — hierarchical scope expansion and validation.

A scope hierarchy lets high-level scopes (e.g. ``"admin"``) implicitly
grant lower-level scopes (e.g. ``"chat"``, ``"embed"``).  This avoids
enumerating every leaf scope at issue time while still allowing
fine-grained validation at the broker level.

The hierarchy is a directed acyclic graph (DAG): each scope maps to a
list of *children* (scopes it implicitly grants).  Cycles are detected
at construction time and raise :class:`ValueError`.

Typical usage::

    from xruntime._runtime._credential._scope_hierarchy import (
        ScopeHierarchy,
    )

    h = ScopeHierarchy({
        "admin": ["chat", "embed", "tool_use"],
        "tool_use": ["chat"],
    })
    # issue with "admin" → credential carries {admin, chat, embed, tool_use}
    expanded = h.expand(["admin"])
    # validate: credential with "admin" satisfies required=["chat"]
    assert h.satisfies(granted=["admin"], required=["chat"])
"""
from __future__ import annotations


class ScopeHierarchy:
    """Hierarchical scope graph with cycle detection.

    Args:
        hierarchy (`dict[str, list[str]]`):
            Mapping from a scope to the list of scopes it implicitly
            grants.  Cycles are detected at construction time.
    """

    def __init__(self, hierarchy: dict[str, list[str]]) -> None:
        """Initialize and validate the hierarchy graph."""
        self._graph: dict[str, list[str]] = {
            k: list(v) for k, v in hierarchy.items()
        }
        self._detect_cycles()

    def expand(self, scopes: list[str]) -> set[str]:
        """Expand scopes via the hierarchy graph.

        Performs a DFS from each input scope, collecting all
        transitively-reachable scopes.  Unknown scopes (not in the
        graph) are preserved as-is.

        Args:
            scopes (`list[str]`):
                The input scopes to expand.

        Returns:
            `set[str]`: The expanded scope set (including originals).
        """
        result: set[str] = set()
        for scope in scopes:
            self._dfs(scope, result, set())
        return result

    def satisfies(
        self,
        granted: list[str],
        required: list[str],
    ) -> bool:
        """Check whether granted scopes satisfy all required scopes.

        Expands ``granted`` via the hierarchy, then checks that every
        scope in ``required`` is present in the expanded set.

        Args:
            granted (`list[str]`):
                The scopes the credential has.
            required (`list[str]`):
                The scopes needed for the operation.

        Returns:
            `bool`: ``True`` if all required scopes are satisfied.
        """
        if not required:
            return True
        expanded = self.expand(granted)
        return all(r in expanded for r in required)

    def _dfs(
        self,
        scope: str,
        result: set[str],
        visiting: set[str],
    ) -> None:
        """Depth-first traversal collecting reachable scopes.

        The caller guards against re-visiting, so we add unconditionally.

        Args:
            scope (`str`): Current scope.
            result (`set[str]`): Accumulator for visited scopes.
            visiting (`set[str]`): Nodes on the current DFS path
                (for cycle detection — should never trigger since
                construction already validated).
        """
        result.add(scope)
        for child in self._graph.get(scope, []):
            if child not in result:
                self._dfs(child, result, visiting | {scope})

    def _detect_cycles(self) -> None:
        """Detect cycles in the hierarchy graph.

        Raises:
            `ValueError`: If a cycle is detected.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {}

        # Initialize all nodes
        for node, children in self._graph.items():
            color.setdefault(node, WHITE)
            for child in children:
                color.setdefault(child, WHITE)

        def dfs(node: str) -> None:
            color[node] = GRAY
            for child in self._graph.get(node, []):
                if color.get(child, WHITE) == GRAY:
                    raise ValueError(
                        f"Scope hierarchy has a cycle involving " f"'{child}'",
                    )
                if color.get(child, WHITE) == WHITE:
                    dfs(child)
            color[node] = BLACK

        for node in list(color.keys()):
            if color.get(node, WHITE) == WHITE:
                dfs(node)


# ── Module-level convenience functions ──────────────────────────


def expand_scopes(
    scopes: list[str],
    hierarchy: dict[str, list[str]],
) -> set[str]:
    """Expand scopes using a hierarchy mapping.

    Args:
        scopes (`list[str]`): The input scopes.
        hierarchy (`dict[str, list[str]]`): The hierarchy mapping.

    Returns:
        `set[str]`: The expanded scope set.
    """
    h = ScopeHierarchy(hierarchy)
    return h.expand(scopes)


def has_scope_with_hierarchy(
    granted: list[str],
    required: str,
    hierarchy: dict[str, list[str]],
) -> bool:
    """Check whether granted scopes satisfy a required scope.

    Args:
        granted (`list[str]`): The scopes the credential has.
        required (`str`): The scope needed.
        hierarchy (`dict[str, list[str]]`): The hierarchy mapping.

    Returns:
        `bool`: ``True`` if the required scope is satisfied.
    """
    h = ScopeHierarchy(hierarchy)
    return h.satisfies(granted=granted, required=[required])
