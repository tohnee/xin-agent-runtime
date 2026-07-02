# -*- coding: utf-8 -*-
"""TDD tests for Prometheus label value escaping in MetricsCollector.

Prometheus exposition format requires the following characters in
label values to be escaped:

    - backslash ``\\`` -> ``\\\\``
    - double quote ``"`` -> ``\\"``
    - newline ``\\n`` -> ``\\\\n``

These tests verify that ``MetricsCollector.export_prometheus`` escapes
all label values (tenant_id, tool, spec, middleware name) correctly.
"""
from __future__ import annotations

import re

from xruntime._infra._metrics import MetricsCollector


def _extract_label_values(text: str) -> list[str]:
    """Extract every ``{...="value"...}`` label value from exposition text.

    Returns the raw (still-escaped) substrings between the innermost
    double quotes of each label pair.
    """
    # Match `key="value"` inside { ... }. The value may contain an
    # escaped quote (\"), so we can't just stop at the first ".
    # We instead walk character-by-character to grab the escaped value.
    values: list[str] = []
    for block in re.finditer(r"\{([^}]*)\}", text):
        inside = block.group(1)
        # Find each key="value" pair.
        i = 0
        while i < len(inside):
            eq = inside.find('="', i)
            if eq == -1:
                break
            start = eq + 2
            j = start
            while j < len(inside):
                if inside[j] == "\\":
                    j += 2  # skip escaped char
                    continue
                if inside[j] == '"':
                    break
                j += 1
            values.append(inside[start:j])
            i = j + 1
    return values


class TestLabelEscaping:
    """Verify Prometheus label value escaping."""

    def test_tenant_id_with_double_quote_escaped(self) -> None:
        """``"`` in tenant_id must be escaped to ``\\"``."""
        c = MetricsCollector()
        # Inject a tenant with a double quote.
        c._active_sessions['ten"ant'] = 1
        text = c.export_prometheus()
        # The raw line must contain the escaped form.
        assert r'tenant="ten\"ant"' in text
        # And must NOT contain the unescaped form.
        assert 'tenant="ten"ant"' not in text

    def test_tenant_id_with_backslash_escaped(self) -> None:
        """``\\`` in tenant_id must be escaped to ``\\\\``."""
        c = MetricsCollector()
        c._active_sessions["ten\\ant"] = 1
        text = c.export_prometheus()
        assert r'tenant="ten\\ant"' in text
        assert 'tenant="ten\\ant"' not in text or (
            # If unescaped, the substring would be different; ensure the
            # escaped form is present and unescaped form is absent.
            r'tenant="ten\\ant"' in text
            and 'tenant="ten"ant"' not in text
        )

    def test_tool_name_with_double_quote_escaped(self) -> None:
        """``"`` in tool name must be escaped."""
        c = MetricsCollector()
        c.record_tool_call('search"query', 1.0)
        text = c.export_prometheus()
        assert r'tool="search\"query"' in text
        assert 'tool="search"query"' not in text

    def test_spec_with_backslash_escaped(self) -> None:
        """``\\`` in spec name must be escaped."""
        c = MetricsCollector()
        c.record_subagent_call("re\\searcher", 1.0, True, 0)
        text = c.export_prometheus()
        assert r'spec="re\\searcher"' in text
        assert 'spec="re\\searcher" status' not in text

    def test_spec_with_double_quote_escaped(self) -> None:
        """``"`` in spec name must be escaped across all subagent metrics."""
        c = MetricsCollector()
        c.record_subagent_call('re"searcher', 1.0, True, 100)
        text = c.export_prometheus()
        assert r'spec="re\"searcher",status="success"' in text
        assert r'spec="re\"searcher"}' in text  # duration / tokens lines

    def test_middleware_name_with_double_quote_escaped(self) -> None:
        """``"`` in middleware name must be escaped."""
        c = MetricsCollector()
        c.record_middleware_latency('Auth"Middleware', 1.0)
        text = c.export_prometheus()
        assert r'middleware="Auth\"Middleware"' in text
        assert 'middleware="Auth"Middleware"' not in text

    def test_middleware_name_with_backslash_escaped(self) -> None:
        """``\\`` in middleware name must be escaped."""
        c = MetricsCollector()
        c.record_middleware_latency("Auth\\Middleware", 1.0)
        text = c.export_prometheus()
        assert r'middleware="Auth\\Middleware"' in text

    def test_normal_values_unchanged(self) -> None:
        """Values without special characters are exported verbatim."""
        c = MetricsCollector()
        c._active_sessions["tenant-1"] = 2
        c.record_tool_call("search_tool", 1.5)
        c.record_tokens("tenant-1", 100, 200)
        c.record_subagent_call("researcher", 1.0, True, 50)
        c.record_middleware_latency("AuthMiddleware", 1.0)
        text = c.export_prometheus()
        assert 'tenant="tenant-1"' in text
        assert 'tool="search_tool"' in text
        assert 'spec="researcher"' in text
        assert 'middleware="AuthMiddleware"' in text

    def test_all_label_values_properly_escaped(self) -> None:
        """No label value in the exported text may contain an unescaped
        ``"`` or a raw ``\\`` that is not part of an escape sequence."""
        c = MetricsCollector()
        c._active_sessions['ten"ant\\bad'] = 1
        c.record_tool_call('to"ol\\x', 1.0)
        c.record_subagent_call('sp"ec\\y', 1.0, True, 10)
        c.record_middleware_latency('mw"\\z', 1.0)
        text = c.export_prometheus()
        # Extract every label value and verify each is escaped correctly.
        for value in _extract_label_values(text):
            # No raw, unescaped double quote should be present inside the
            # value (it would have terminated the value during extraction,
            # so this is a sanity check).
            # An unescaped backslash (one not followed by " or \ or n)
            # would indicate missing escaping.
            i = 0
            while i < len(value):
                if value[i] == "\\":
                    # Must be followed by one of: " \ n
                    assert i + 1 < len(
                        value
                    ), f"Dangling backslash in label value: {value!r}"
                    assert value[i + 1] in (
                        '"',
                        "\\",
                        "n",
                    ), f"Invalid escape sequence in label value: {value!r}"
                    i += 2
                    continue
                assert (
                    value[i] != '"'
                ), f"Unescaped double quote in label value: {value!r}"
                i += 1

    def test_tenant_id_with_newline_escaped(self) -> None:
        """A literal newline in tenant_id must be escaped to ``\\\\n``."""
        c = MetricsCollector()
        c._active_sessions["ten\nant"] = 1
        text = c.export_prometheus()
        # The escaped form: tenant="ten\nant"
        assert 'tenant="ten\\nant"' in text
        # No raw newline inside the label value.
        # Find the metric line and ensure it does not contain \n in the
        # middle of the value.
        line = next(
            (l for l in text.splitlines() if "xruntime_active_sessions" in l),
            "",
        )
        assert "\n" not in line  # the line itself was split correctly
        assert r"\"ten\nant\"" not in line  # not double-escaped weirdly
