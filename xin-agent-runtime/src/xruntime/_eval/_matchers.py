# -*- coding: utf-8 -*-
"""Assertion matchers for the Evals DSL.

Mirrors Eve's matcher API but with Python snake_case names where
idiomatic.  All matchers return ``(bool, str)`` — never raise.
"""
from __future__ import annotations

import re
from typing import Any


class Matcher:
    """Base matcher (Eve parity).

    Subclasses implement :meth:`match` returning a
    ``(passed, message)`` tuple.  Matchers never raise.
    """

    def match(self, value: Any) -> tuple[bool, str]:
        """Return ``(passed, failure_message)``.

        Args:
            value (`Any`): The value to match against.

        Returns:
            `tuple[bool, str]`: Whether the value matched, and a
            failure message (empty when passed).
        """
        raise NotImplementedError


class includes(Matcher):  # noqa: N801 — lowercase to match Eve API
    """Substring matcher (Eve parity, lowercase name).

    Args:
        needle (`str`): The substring to look for.
    """

    def __init__(self, needle: str) -> None:
        self.needle = needle

    def match(self, value: Any) -> tuple[bool, str]:
        ok = self.needle in str(value)
        return ok, "" if ok else f"missing {self.needle!r}"


class matches_regex(Matcher):
    """Regex matcher.

    Args:
        pattern (`str`): The regex pattern.
    """

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern

    def match(self, value: Any) -> tuple[bool, str]:
        ok = re.search(self.pattern, str(value)) is not None
        return ok, "" if ok else f"no match for /{self.pattern}/"


class equals(Matcher):
    """Exact equality matcher.

    Args:
        expected (`Any`): The expected value.
    """

    def __init__(self, expected: Any) -> None:
        self.expected = expected

    def match(self, value: Any) -> tuple[bool, str]:
        ok = value == self.expected
        return ok, "" if ok else f"got {value!r}"


class not_contains(Matcher):
    """Negated substring matcher.

    Args:
        needle (`str`): The substring that must NOT appear.
    """

    def __init__(self, needle: str) -> None:
        self.needle = needle

    def match(self, value: Any) -> tuple[bool, str]:
        ok = self.needle not in str(value)
        return ok, "" if ok else f"found forbidden {self.needle!r}"


class has_keys(Matcher):
    """Dict-key presence matcher (for tool_input_matches).

    Args:
        keys (`list[str]`): Keys that must be present.
    """

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def match(self, value: Any) -> tuple[bool, str]:
        missing = [k for k in self.keys if k not in (value or {})]
        return (not missing, f"missing keys {missing}")
