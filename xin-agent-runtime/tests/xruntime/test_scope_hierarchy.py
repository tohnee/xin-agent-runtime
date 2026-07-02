# -*- coding: utf-8 -*-
"""TDD tests for ScopeHierarchy (P3-C Task 1).

Covers scope hierarchy expansion, validation, and cycle detection.

A scope hierarchy lets high-level scopes (e.g. "admin") implicitly
grant lower-level scopes (e.g. "chat", "embed").  This avoids
enumerating every leaf scope at issue time while still allowing
fine-grained validation.
"""
from __future__ import annotations

import pytest

from xruntime._runtime._credential._scope_hierarchy import (
    ScopeHierarchy,
    expand_scopes,
    has_scope_with_hierarchy,
)


# ── 1. Hierarchy expansion ──────────────────────────────────────


class TestScopeHierarchyExpand:
    """ScopeHierarchy — expand scopes via hierarchy."""

    def test_expand_admin_includes_all_children(self) -> None:
        """admin 包含 chat + embed + tool_use."""
        h = ScopeHierarchy({"admin": ["chat", "embed", "tool_use"]})
        result = h.expand(["admin"])
        assert result == {"admin", "chat", "embed", "tool_use"}

    def test_expand_tool_use_includes_chat(self) -> None:
        """tool_use 包含 chat."""
        h = ScopeHierarchy({"tool_use": ["chat"]})
        result = h.expand(["tool_use"])
        assert result == {"tool_use", "chat"}

    def test_expand_leaf_scope_returns_self(self) -> None:
        """叶子 scope 只返回自身."""
        h = ScopeHierarchy({"tool_use": ["chat"]})
        result = h.expand(["chat"])
        assert result == {"chat"}

    def test_expand_unknown_scope_returns_self(self) -> None:
        """未知 scope 保留,不报错."""
        h = ScopeHierarchy({"admin": ["chat"]})
        result = h.expand(["unknown"])
        assert result == {"unknown"}

    def test_expand_multiple_scopes_union(self) -> None:
        """多个 scope 展开后取并集."""
        h = ScopeHierarchy(
            {"admin": ["chat"], "tool_use": ["embed"]},
        )
        result = h.expand(["admin", "tool_use"])
        assert result == {"admin", "chat", "tool_use", "embed"}

    def test_expand_empty_hierarchy_returns_input(self) -> None:
        """空 hierarchy 时返回原始 scope."""
        h = ScopeHierarchy({})
        result = h.expand(["chat", "embed"])
        assert result == {"chat", "embed"}


# ── 2. Hierarchy validation ─────────────────────────────────────


class TestScopeHierarchyValidate:
    """ScopeHierarchy — validate required scopes against granted."""

    def test_validate_admin_satisfies_chat(self) -> None:
        """有 admin 的凭证自动满足 required=["chat"]."""
        h = ScopeHierarchy({"admin": ["chat", "embed"]})
        assert h.satisfies(granted=["admin"], required=["chat"]) is True

    def test_validate_tool_use_satisfies_chat(self) -> None:
        """有 tool_use 的凭证自动满足 required=["chat"]."""
        h = ScopeHierarchy({"tool_use": ["chat"]})
        assert h.satisfies(granted=["tool_use"], required=["chat"]) is True

    def test_validate_chat_does_not_satisfy_admin(self) -> None:
        """有 chat 的凭证不满足 required=["admin"]."""
        h = ScopeHierarchy({"admin": ["chat"]})
        assert h.satisfies(granted=["chat"], required=["admin"]) is False

    def test_validate_no_hierarchy_falls_back_to_exact(self) -> None:
        """无 hierarchy 时退化为精确匹配."""
        h = ScopeHierarchy({})
        assert h.satisfies(granted=["chat"], required=["chat"]) is True
        assert h.satisfies(granted=["chat"], required=["embed"]) is False

    def test_validate_empty_required_returns_true(self) -> None:
        """required 为空时永远返回 True(无需任何 scope)."""
        h = ScopeHierarchy({"admin": ["chat"]})
        assert h.satisfies(granted=[], required=[]) is True
        assert h.satisfies(granted=["chat"], required=[]) is True


# ── 3. Cycle detection ──────────────────────────────────────────


class TestScopeHierarchyCycleDetection:
    """ScopeHierarchy — detect cycles in the hierarchy graph."""

    def test_cycle_detection_raises(self) -> None:
        """a→b→a 循环必须抛 ValueError."""
        with pytest.raises(ValueError, match="cycle"):
            ScopeHierarchy({"a": ["b"], "b": ["a"]})

    def test_self_cycle_detection_raises(self) -> None:
        """a→a 自循环必须抛 ValueError."""
        with pytest.raises(ValueError, match="cycle"):
            ScopeHierarchy({"a": ["a"]})


# ── 4. Deep nesting & diamond ───────────────────────────────────


class TestScopeHierarchyDeepNesting:
    """ScopeHierarchy — deep chains and diamond dependencies."""

    def test_three_level_chain_expansion(self) -> None:
        """a→b→c 三层嵌套展开."""
        h = ScopeHierarchy({"a": ["b"], "b": ["c"]})
        result = h.expand(["a"])
        assert result == {"a", "b", "c"}

    def test_diamond_dependency_no_duplicates(self) -> None:
        """菱形依赖:a→b, a→c, b→d, c→d → d 不重复."""
        h = ScopeHierarchy(
            {"a": ["b", "c"], "b": ["d"], "c": ["d"]},
        )
        result = h.expand(["a"])
        assert result == {"a", "b", "c", "d"}

    def test_empty_scopes_input_returns_empty_set(self) -> None:
        """expand([]) 返回空集合."""
        h = ScopeHierarchy({"admin": ["chat"]})
        result = h.expand([])
        assert result == set()


# ── 5. Module-level convenience functions ───────────────────────


class TestScopeHierarchyConvenienceFunctions:
    """Module-level expand_scopes / has_scope_with_hierarchy."""

    def test_expand_scopes_function(self) -> None:
        """模块级 expand_scopes 函数."""
        hierarchy = {"admin": ["chat", "embed"]}
        result = expand_scopes(["admin"], hierarchy)
        assert result == {"admin", "chat", "embed"}

    def test_has_scope_with_hierarchy_function(self) -> None:
        """模块级 has_scope_with_hierarchy 函数."""
        hierarchy = {"admin": ["chat"]}
        assert (
            has_scope_with_hierarchy(
                granted=["admin"],
                required="chat",
                hierarchy=hierarchy,
            )
            is True
        )
        assert (
            has_scope_with_hierarchy(
                granted=["chat"],
                required="admin",
                hierarchy=hierarchy,
            )
            is False
        )
