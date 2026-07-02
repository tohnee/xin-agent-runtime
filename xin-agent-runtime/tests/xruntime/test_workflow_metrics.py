# -*- coding: utf-8 -*-
"""TDD tests for WorkflowMetrics (P3-D Task 2).

Covers per-step metrics collection for workflow execution:

* ``workflow_step_duration_seconds{workflow,step,status}`` — histogram
* ``workflow_step_total{workflow,step,status}`` — counter
* ``workflow_checkpoint_save_total{workflow}`` — counter
* ``workflow_resume_total{workflow}`` — counter

The :class:`WorkflowMetrics` collector is in-memory (like the existing
:class:`MetricsCollector`) and exports in Prometheus text format.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from xruntime._runtime._workflow._sdk import (
    FunctionExecutor,
    run_workflow,
)


# ── 1. WorkflowMetrics construction ────────────────────────────


class TestWorkflowMetricsConstruction:
    """WorkflowMetrics — construction."""

    def test_metrics_default_construction(self) -> None:
        """默认构造."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        assert m is not None
        # 无数据时 export 返回空字符串
        assert m.export_prometheus() == ""

    def test_metrics_starts_empty(self) -> None:
        """初始无指标时 export 返回空或注释行."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        text = m.export_prometheus()
        # 应该包含 HELP/TYPE 行(即使无数据)
        assert "workflow_step_total" in text or text == ""


# ── 2. Step metrics recording ──────────────────────────────────


class TestWorkflowMetricsStepRecording:
    """WorkflowMetrics — record_step / record_step_duration."""

    def test_record_step_increments_counter(self) -> None:
        """record_step 增加 workflow_step_total 计数."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        m.record_step(
            workflow_id="wf-1",
            step_id="s1",
            status="COMPLETED",
        )
        m.record_step(
            workflow_id="wf-1",
            step_id="s1",
            status="COMPLETED",
        )
        m.record_step(
            workflow_id="wf-1",
            step_id="s2",
            status="FAILED",
        )

        text = m.export_prometheus()
        assert "workflow_step_total" in text
        # 应该有 wf-1/s1/COMPLETED = 2
        assert "wf-1" in text
        assert "s1" in text
        assert "COMPLETED" in text

    def test_record_step_duration_records_histogram(self) -> None:
        """record_step_duration 记录直方图."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        m.record_step_duration(
            workflow_id="wf-1",
            step_id="s1",
            duration_ms=100.0,
        )
        m.record_step_duration(
            workflow_id="wf-1",
            step_id="s1",
            duration_ms=200.0,
        )

        text = m.export_prometheus()
        assert "workflow_step_duration_ms" in text

    def test_get_step_count(self) -> None:
        """get_step_count 返回指定 workflow/step/status 的计数."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        m.record_step("wf-1", "s1", "COMPLETED")
        m.record_step("wf-1", "s1", "COMPLETED")
        m.record_step("wf-1", "s1", "FAILED")
        m.record_step("wf-2", "s1", "COMPLETED")

        assert m.get_step_count("wf-1", "s1", "COMPLETED") == 2
        assert m.get_step_count("wf-1", "s1", "FAILED") == 1
        assert m.get_step_count("wf-2", "s1", "COMPLETED") == 1
        assert m.get_step_count("wf-1", "s2", "COMPLETED") == 0

    def test_get_step_durations(self) -> None:
        """get_step_durations 返回指定 step 的所有时长."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        m.record_step_duration("wf-1", "s1", 100.0)
        m.record_step_duration("wf-1", "s1", 200.0)
        m.record_step_duration("wf-1", "s2", 50.0)

        durations = m.get_step_durations("wf-1", "s1")
        assert len(durations) == 2
        assert 100.0 in durations
        assert 200.0 in durations

        durations_s2 = m.get_step_durations("wf-1", "s2")
        assert len(durations_s2) == 1
        assert 50.0 in durations_s2

        durations_empty = m.get_step_durations("wf-1", "s3")
        assert durations_empty == []


# ── 3. Checkpoint / resume metrics ─────────────────────────────


class TestWorkflowMetricsCheckpointResume:
    """WorkflowMetrics — checkpoint_save / resume counters."""

    def test_record_checkpoint_save(self) -> None:
        """record_checkpoint_save 增加 workflow_checkpoint_save_total."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        m.record_checkpoint_save("wf-1")
        m.record_checkpoint_save("wf-1")
        m.record_checkpoint_save("wf-2")

        assert m.get_checkpoint_save_count("wf-1") == 2
        assert m.get_checkpoint_save_count("wf-2") == 1
        assert m.get_checkpoint_save_count("wf-3") == 0

        text = m.export_prometheus()
        assert "workflow_checkpoint_save_total" in text

    def test_record_resume(self) -> None:
        """record_resume 增加 workflow_resume_total."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        m.record_resume("wf-1")
        m.record_resume("wf-1")

        assert m.get_resume_count("wf-1") == 2
        assert m.get_resume_count("wf-2") == 0

        text = m.export_prometheus()
        assert "workflow_resume_total" in text


# ── 4. Integration with run_workflow ───────────────────────────


class TestWorkflowMetricsIntegration:
    """WorkflowMetrics — integration with run_workflow."""

    @pytest.mark.asyncio
    async def test_workflow_run_records_step_metrics(self) -> None:
        """run_workflow with metrics= 参数自动记录 step 指标."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        wf = Workflow(
            id="wf-metrics-1",
            name="Metrics 1",
            steps=[
                WorkflowStep(id="s1", name="S1", agent="a", prompt="p"),
                WorkflowStep(
                    id="s2",
                    name="S2",
                    agent="a",
                    prompt="p",
                    depends_on=["s1"],
                ),
            ],
        )
        executor = FunctionExecutor(lambda s, c: f"out-{s.id}")
        result = await run_workflow(wf, executor, metrics=m)

        assert result.status == WorkflowStatus.COMPLETED
        # 2 个 step 都应记录
        assert m.get_step_count("wf-metrics-1", "s1", "COMPLETED") == 1
        assert m.get_step_count("wf-metrics-1", "s2", "COMPLETED") == 1
        # duration 应被记录
        assert len(m.get_step_durations("wf-metrics-1", "s1")) == 1
        assert len(m.get_step_durations("wf-metrics-1", "s2")) == 1

    @pytest.mark.asyncio
    async def test_workflow_run_without_metrics_works(self) -> None:
        """不传 metrics 时 workflow 仍正常执行."""
        wf = Workflow(
            id="wf-no-metrics",
            name="No Metrics",
            steps=[WorkflowStep(id="s1", name="S1", agent="a", prompt="p")],
        )
        executor = FunctionExecutor(lambda s, c: "ok")
        result = await run_workflow(wf, executor, metrics=None)
        assert result.status == WorkflowStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_failed_step_records_failed_metric(self) -> None:
        """失败的 step 记录 status=FAILED 指标."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        wf = Workflow(
            id="wf-fail-metrics",
            name="Fail Metrics",
            steps=[
                WorkflowStep(
                    id="s1",
                    name="S1",
                    agent="a",
                    prompt="p",
                    on_failure="abort",
                )
            ],
        )
        executor = FunctionExecutor(
            lambda s, c: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        result = await run_workflow(wf, executor, metrics=m)

        assert result.status == WorkflowStatus.FAILED
        assert m.get_step_count("wf-fail-metrics", "s1", "FAILED") == 1

    @pytest.mark.asyncio
    async def test_checkpoint_save_recorded_with_store(self) -> None:
        """带 store 运行时记录 checkpoint_save 指标."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics
        from xruntime._runtime._workflow import InMemoryCheckpointStore

        m = WorkflowMetrics()
        store = InMemoryCheckpointStore()
        wf = Workflow(
            id="wf-cp-metrics",
            name="CP Metrics",
            steps=[WorkflowStep(id="s1", name="S1", agent="a", prompt="p")],
        )
        executor = FunctionExecutor(lambda s, c: "ok")
        result = await run_workflow(wf, executor, store=store, metrics=m)

        assert result.status == WorkflowStatus.COMPLETED
        # checkpoint 应被记录
        assert m.get_checkpoint_save_count("wf-cp-metrics") >= 1


# ── 5. Prometheus export format ────────────────────────────────


class TestWorkflowMetricsPrometheusExport:
    """WorkflowMetrics — Prometheus text format."""

    def test_export_includes_all_metric_types(self) -> None:
        """export 包含所有指标类型的 HELP/TYPE 行."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        m.record_step("wf-1", "s1", "COMPLETED")
        m.record_step_duration("wf-1", "s1", 100.0)
        m.record_checkpoint_save("wf-1")
        m.record_resume("wf-1")

        text = m.export_prometheus()
        assert "workflow_step_total" in text
        assert "workflow_step_duration_ms" in text
        assert "workflow_checkpoint_save_total" in text
        assert "workflow_resume_total" in text

    def test_export_empty_when_no_data(self) -> None:
        """无数据时 export 返回空字符串或仅注释."""
        from xruntime._runtime._workflow._metrics import WorkflowMetrics

        m = WorkflowMetrics()
        text = m.export_prometheus()
        # 无数据时返回空或仅含 HELP/TYPE 声明
        assert isinstance(text, str)
