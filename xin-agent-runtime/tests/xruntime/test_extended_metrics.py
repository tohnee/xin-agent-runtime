# -*- coding: utf-8 -*-
"""TDD tests for extended WorkflowMetrics (P4-A metrics gap)."""
from __future__ import annotations

import time

import pytest

from xruntime._runtime._workflow._metrics import WorkflowMetrics


class TestExtendedMetrics:
    """Extended metrics — 6 new gauges/counters."""

    def test_credential_expiry_gauge(self) -> None:
        m = WorkflowMetrics()
        m.record_credential_expiry("cred-1", time.time() + 300)
        text = m.export_prometheus()
        assert "xruntime_credential_expiry_seconds" in text
        assert "cred-1" in text

    def test_auto_rotation_failures_counter(self) -> None:
        m = WorkflowMetrics()
        m.record_auto_rotation_failure()
        m.record_auto_rotation_failure()
        text = m.export_prometheus()
        assert "xruntime_auto_rotation_failures_total" in text
        assert "2" in text

    def test_approval_timeout_counter(self) -> None:
        m = WorkflowMetrics()
        m.record_approval_timeout()
        text = m.export_prometheus()
        assert "xruntime_approval_timed_out_total" in text

    def test_approval_pending_gauge(self) -> None:
        m = WorkflowMetrics()
        m.set_approval_pending("alice", 5)
        m.set_approval_pending("bob", 3)
        text = m.export_prometheus()
        assert "xruntime_approval_pending_count" in text
        assert "alice" in text
        assert "bob" in text

    def test_redis_pool_gauge(self) -> None:
        m = WorkflowMetrics()
        m.set_redis_pool_stats(active=8, max_connections=10)
        text = m.export_prometheus()
        assert "xruntime_redis_pool_active_connections" in text
        assert "xruntime_redis_pool_max_connections" in text

    def test_all_metrics_exported_together(self) -> None:
        """All 10+ metrics in one export."""
        m = WorkflowMetrics()
        m.record_step("wf", "s1", "COMPLETED")
        m.record_step_duration("wf", "s1", 100.0)
        m.record_checkpoint_save("wf")
        m.record_resume("wf")
        m.record_credential_expiry("cred-1", time.time() + 300)
        m.record_auto_rotation_failure()
        m.record_approval_timeout()
        m.set_approval_pending("alice", 2)
        m.set_redis_pool_stats(active=5, max_connections=10)
        text = m.export_prometheus()
        for metric in [
            "workflow_step_total",
            "workflow_step_duration_ms",
            "workflow_checkpoint_save_total",
            "workflow_resume_total",
            "xruntime_credential_expiry_seconds",
            "xruntime_auto_rotation_failures_total",
            "xruntime_approval_timed_out_total",
            "xruntime_approval_pending_count",
            "xruntime_redis_pool_active_connections",
            "xruntime_redis_pool_max_connections",
        ]:
            assert metric in text, f"Missing metric: {metric}"
