# -*- coding: utf-8 -*-
"""Alert simulation tests — verify Prometheus alert conditions.

Simulates the scenarios that would trigger each alert rule and
verifies the underlying metrics are correctly recorded.
"""
from __future__ import annotations

import json
import pytest

from xruntime._runtime._workflow._metrics import WorkflowMetrics
from xruntime._runtime._workflow._audit import WorkflowAuditLogger


class TestAlertSimulation:
    """Simulate alert trigger scenarios."""

    def test_alert1_workflow_high_failure_rate(self):
        """告警1: 失败率 > 5% — 验证 FAILED 指标被记录."""
        m = WorkflowMetrics()
        for i in range(94):
            m.record_step("wf", f"s{i}", "COMPLETED")
        for i in range(6):
            m.record_step("wf", f"s{i+94}", "FAILED")

        text = m.export_prometheus()
        assert 'status="FAILED"' in text
        assert 'status="COMPLETED"' in text

    def test_alert3_checkpoint_save_failure(self):
        """告警3: 有 step 但无 checkpoint — 验证指标缺失."""
        m = WorkflowMetrics()
        m.record_step("wf", "s1", "COMPLETED")
        # 不调用 record_checkpoint_save
        assert m.get_checkpoint_save_count("wf") == 0
        text = m.export_prometheus()
        assert "workflow_step_total" in text
        assert "workflow_checkpoint_save_total" not in text

    def test_alert3_checkpoint_present_when_saved(self):
        """告警3 反向: 有 checkpoint save 时指标存在."""
        m = WorkflowMetrics()
        m.record_step("wf", "s1", "COMPLETED")
        m.record_checkpoint_save("wf")
        assert m.get_checkpoint_save_count("wf") == 1
        text = m.export_prometheus()
        assert "workflow_checkpoint_save_total" in text

    def test_audit_compliance_no_api_key(self):
        """审计合规: 审计条目不含 api_key."""
        audit = WorkflowAuditLogger()
        audit.record_step(
            "wf-1",
            "s1",
            "coder",
            "COMPLETED",
            123.4,
            "tenant-1",
        )
        text = json.dumps(audit.get_entries())
        assert "api_key" not in text
        assert "secret" not in text.lower()

    def test_audit_compliance_no_secret(self):
        """审计合规: 审计条目不含 secret."""
        audit = WorkflowAuditLogger()
        audit.record_step(
            "wf-secret",
            "s1",
            "coder",
            "COMPLETED",
            100.0,
            "t1",
        )
        entries = audit.get_entries()
        for e in entries:
            for key, val in e.items():
                if isinstance(val, str):
                    assert "sk-" not in val
                    assert "password" not in val.lower()

    def test_metrics_export_contains_all_alert_sources(self):
        """所有告警引用的指标都能从 export_prometheus() 获取."""
        m = WorkflowMetrics()
        m.record_step("wf", "s1", "COMPLETED")
        m.record_step_duration("wf", "s1", 100.0)
        m.record_checkpoint_save("wf")
        m.record_resume("wf")

        text = m.export_prometheus()
        # 告警1: workflow_step_total
        assert "workflow_step_total" in text
        # 告警2: workflow_step_duration_ms
        assert "workflow_step_duration_ms" in text
        # 告警3: workflow_checkpoint_save_total
        assert "workflow_checkpoint_save_total" in text
        # resume 指标
        assert "workflow_resume_total" in text

    @pytest.mark.asyncio
    async def test_smoke_observability_integration(self):
        """Smoke test: tracer + metrics + audit 全链路集成."""
        from xruntime._runtime._workflow import (
            WorkflowBuilder,
            FunctionExecutor,
            run_workflow,
            WorkflowTracer,
            WorkflowMetrics,
            WorkflowAuditLogger,
        )

        wf = (
            WorkflowBuilder()
            .id("smoke-test")
            .step(id="s1", agent="a", prompt="p")
            .build()
        )
        tracer = WorkflowTracer(endpoint="")
        metrics = WorkflowMetrics()
        audit = WorkflowAuditLogger()
        result = await run_workflow(
            wf,
            FunctionExecutor(lambda s, c: "ok"),
            tracer=tracer,
            metrics=metrics,
            audit=audit,
        )

        assert result.status == "COMPLETED"
        # Tracer: root + step span
        spans = tracer.get_recorded_spans()
        assert len(spans) >= 2
        assert spans[0]["name"] == "workflow.run"
        # Metrics: step recorded
        assert metrics.get_step_count("smoke-test", "s1", "COMPLETED") == 1
        # Audit: step execution logged
        entries = audit.get_entries()
        step_entries = [
            e for e in entries if e["action"] == "workflow.step.executed"
        ]
        assert len(step_entries) == 1
        assert step_entries[0]["step_id"] == "s1"
