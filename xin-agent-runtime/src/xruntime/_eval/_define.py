# -*- coding: utf-8 -*-
"""``@define_eval`` decorator + module-level registry.

Mirrors Eve's ``defineEval`` but as a Python decorator.  The
decorated function receives an :class:`EvalContext` and may call
``t.send`` / ``t.check`` / ``t.called_tool`` / ``t.as_tenant``.

The decorator returns an :class:`EvalSpec` (not the original
function) so the spec is self-describing for the collector.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ._models import EvalSpec

# Module-level registry.  Every @define_eval appends to this list.
# EvalCollector imports user modules, which triggers the decorator,
# which populates this registry.  The collector then reads it.
_REGISTRY: list[EvalSpec] = []


def define_eval(
    description: str,
    *,
    domain: str = "general",
    tags: tuple[str, ...] = ("offline",),
) -> Callable[[Callable[[Any], Awaitable[None]]], EvalSpec]:
    """Declare an agent behavior eval.

    Args:
        description (`str`): Human-readable spec; shown in reports.
        domain (`str`): Grouping key for the test suite.
        tags (`tuple`): ``offline`` (default) runs in CI without
            API keys; ``online`` requires a real model.

    Returns:
        `Callable`: A decorator that converts the async function
        into an :class:`EvalSpec` and registers it.
    """

    def _wrap(
        fn: Callable[[Any], Awaitable[None]],
    ) -> EvalSpec:
        spec = EvalSpec(
            eval_id=f"{domain}.{fn.__name__}",
            description=description,
            domain=domain,
            tags=list(tags),
            fn=fn,
        )
        _REGISTRY.append(spec)
        return spec

    return _wrap


def _clear_registry() -> None:
    """Clear the registry (used by tests to avoid cross-test pollution)."""
    _REGISTRY.clear()


def _get_registry() -> list[EvalSpec]:
    """Return a copy of the current registry (for collector / tests)."""
    return list(_REGISTRY)
