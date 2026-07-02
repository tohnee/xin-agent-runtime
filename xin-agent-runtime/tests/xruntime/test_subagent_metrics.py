# -*- coding: utf-8 -*-
"""Tests for MetricsCollector sub-agent metrics."""
from __future__ import annotations

from xruntime._infra._metrics import MetricsCollector


class TestSubagentMetrics:
    """Sub-agent specific metrics tests."""

    def test_record_subagent_call(self) -> None:
        c = MetricsCollector()
        c.record_subagent_call(
            spec_name="researcher",
            duration_seconds=1.5,
            success=True,
            token_usage=500,
        )
        stats = c.subagent_stats("researcher")
        assert stats["count"] == 1
        assert stats["successes"] == 1
        assert stats["failures"] == 0
        assert stats["avg_duration_seconds"] == 1.5
        assert stats["total_tokens"] == 500

    def test_record_multiple_calls(self) -> None:
        c = MetricsCollector()
        c.record_subagent_call("coder", 2.0, True, 1000)
        c.record_subagent_call("coder", 4.0, False, 500)
        stats = c.subagent_stats("coder")
        assert stats["count"] == 2
        assert stats["successes"] == 1
        assert stats["failures"] == 1
        assert stats["avg_duration_seconds"] == 3.0
        assert stats["total_tokens"] == 1500

    def test_subagent_stats_nonexistent(self) -> None:
        c = MetricsCollector()
        stats = c.subagent_stats("nonexistent")
        assert stats["count"] == 0
        assert stats["avg_duration_seconds"] == 0.0

    def test_prometheus_includes_subagent_metrics(self) -> None:
        c = MetricsCollector()
        c.record_subagent_call("researcher", 1.5, True, 500)
        text = c.export_prometheus()
        assert "xruntime_subagent_calls_total" in text
        assert 'spec="researcher"' in text
        assert 'status="success"' in text
        assert "xruntime_subagent_duration_seconds" in text
        assert "xruntime_subagent_tokens_total" in text

    def test_prometheus_subagent_failure(self) -> None:
        c = MetricsCollector()
        c.record_subagent_call("coder", 0.5, False, 0)
        text = c.export_prometheus()
        assert 'status="failure"' in text

    def test_prometheus_no_subagent_data(self) -> None:
        c = MetricsCollector()
        text = c.export_prometheus()
        assert "xruntime_subagent_calls_total" in text
        assert "xruntime_subagent_duration_seconds" in text
