# -*- coding: utf-8 -*-
"""XRuntime Evals framework.

Public surface:

* :func:`define_eval` — declare an agent behavior eval.
* :class:`EvalRunner` — collect and run evals.
* Matchers: :class:`includes`, :class:`matches_regex`,
  :class:`equals`, :class:`not_contains`, :class:`has_keys`.

Usage::

    from xruntime._eval import define_eval, includes

    @define_eval("Agent greets", domain="smoke", tags=("offline",))
    async def test_greet(t):
        await t.send("hello")
        t.reply_contains("hi")
"""
from __future__ import annotations

from ._define import define_eval
from ._matchers import (
    Matcher,
    equals,
    has_keys,
    includes,
    matches_regex,
    not_contains,
)
from ._models import (
    AssertionResult,
    EvalResult,
    EvalSpec,
    EvalStatus,
)

__all__ = [
    "AssertionResult",
    "EvalResult",
    "EvalSpec",
    "EvalStatus",
    "EvalRunner",
    "Matcher",
    "define_eval",
    "equals",
    "has_keys",
    "includes",
    "matches_regex",
    "not_contains",
]


def __getattr__(name: str):
    """Lazy-import EvalRunner to avoid heavy deps on package import."""
    if name == "EvalRunner":
        from ._runner import EvalRunner

        return EvalRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
