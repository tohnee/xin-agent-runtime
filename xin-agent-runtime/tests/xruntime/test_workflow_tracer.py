# -*- coding: utf-8 -*-
"""TDD tests for WorkflowTracer (P3-D Task 1).

Covers OpenTelemetry distributed tracing for workflow execution:

* Root span ``workflow.run`` for the whole workflow.
* Child spans ``workflow.step.<id>`` per step.
* Span attributes: ``workflow.id``, ``step.id``, ``step.agent``,
  ``step.status``, ``step.duration_ms``.
* Span events: ``step.started``, ``step.completed``, ``step.failed``.
* No-op tracer when OTel is not installed (graceful degradation).
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

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


# ── 1. WorkflowTracer construction ────────────────────────────


class TestWorkflowTracerConstruction:
    """WorkflowTracer — construction."""

    def test_tracer_with_service_name(self) -> None:
        """构造: service_name + endpoint."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer(
            service_name="xruntime-workflow",
            endpoint="http://localhost:4317",
        )
        assert tracer.service_name == "xruntime-workflow"
        assert tracer.endpoint == "http://localhost:4317"

    def test_tracer_default_service_name(self) -> None:
        """默认 service_name="xruntime-workflow"."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer()
        assert tracer.service_name == "xruntime-workflow"

    def test_tracer_no_endpoint_means_noop(self) -> None:
        """endpoint="" 时使用 in-memory tracer(不导出 spans)."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer(endpoint="")
        # in-memory tracer 仍可记录 spans(但不导出)
        assert tracer._otel_available is False
        assert tracer._otel_tracer is None

    def test_tracer_otel_unavailable_graceful_degradation(self) -> None:
        """OTel 未安装时,no-op tracer 仍可工作."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer(endpoint="")
        # 不应抛异常
        assert tracer is not None

    def test_tracer_with_endpoint_attempts_otel_setup(self) -> None:
        """endpoint 设置时尝试 OTel setup(可能失败但不崩溃)."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        # 传入一个不可达端点 — setup 可能成功或失败,但不应抛异常
        tracer = WorkflowTracer(
            service_name="test-svc",
            endpoint="http://localhost:9999",
        )
        # tracer 仍可工作(in-memory fallback)
        assert tracer.service_name == "test-svc"
        # OTel 状态可能 True 或 False(取决于环境),但不崩溃
        spans = tracer.get_recorded_spans()
        assert spans == []

    def test_tracer_endpoint_property(self) -> None:
        """endpoint 属性返回构造时传入的值."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer(endpoint="http://otel:4317")
        assert tracer.endpoint == "http://otel:4317"

    def test_inmemory_tracer_records_spans_directly(self) -> None:
        """InMemoryWorkflowTracer 直接记录 spans."""
        from xruntime._runtime._workflow._tracer import (
            InMemoryWorkflowTracer,
        )

        tracer = InMemoryWorkflowTracer()
        span = tracer.start_workflow_span("wf-1", "Test")
        with span:
            pass
        spans = tracer.get_recorded_spans()
        assert len(spans) == 1
        assert spans[0]["name"] == "workflow.run"
        assert spans[0]["attributes"]["workflow.id"] == "wf-1"

    def test_tracer_otel_import_error_falls_back_gracefully(
        self,
    ) -> None:
        """OTel import 失败时,in-memory fallback 正常工作."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        # Mock sys.modules 让 opentelemetry 导入失败
        import sys

        original = sys.modules.get("opentelemetry")
        try:
            # 暂时移除 opentelemetry 模块
            mods_to_remove = [
                k for k in sys.modules if k.startswith("opentelemetry")
            ]
            saved = {k: sys.modules.pop(k) for k in mods_to_remove}

            # 让 import opentelemetry 抛 ImportError
            import builtins

            original_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name.startswith("opentelemetry"):
                    raise ImportError("mocked")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = mock_import
            try:
                tracer = WorkflowTracer(
                    endpoint="http://localhost:4317",
                )
                # 应该 fallback 到 in-memory
                assert tracer._otel_available is False
            finally:
                builtins.__import__ = original_import
                # 恢复模块
                sys.modules.update(saved)
        finally:
            if original is not None:
                sys.modules["opentelemetry"] = original

    def test_tracer_otel_runtime_error_falls_back(self) -> None:
        """OTel setup 抛非 ImportError 异常时,in-memory fallback."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer
        from unittest.mock import patch

        # Mock opentelemetry.trace.set_tracer_provider 抛 RuntimeError
        def mock_setup_fail(endpoint):
            raise RuntimeError("unexpected OTel setup failure")

        with patch.object(
            WorkflowTracer,
            "_try_setup_otel",
            side_effect=mock_setup_fail,
        ):
            # _try_setup_otel 在 __init__ 中被调用,异常会传播
            # 但我们的实现已经 catch 了 except Exception
            # 所以这里直接测试 _try_setup_otel 不被 catch 的情况
            # 实际上 _try_setup_otel 内部 catch,所以不会传播
            pass

        # 直接测试:让 _try_setup_otel 内部的代码抛 RuntimeError
        # 方法是 mock opentelemetry.trace.set_tracer_provider
        import sys

        original = sys.modules.get("opentelemetry")
        try:
            # 确保 opentelemetry 可以 import
            if "opentelemetry" not in sys.modules:
                import opentelemetry  # noqa: F401

            from unittest.mock import MagicMock as _MagicMock
            import opentelemetry.trace as otel_trace

            original_set = otel_trace.set_tracer_provider
            otel_trace.set_tracer_provider = _MagicMock(
                side_effect=RuntimeError("boom"),
            )
            try:
                tracer = WorkflowTracer(
                    endpoint="http://localhost:4317",
                )
                # 异常被 catch,_otel_available=False
                assert tracer._otel_available is False
            finally:
                otel_trace.set_tracer_provider = original_set
        finally:
            if original is not None:
                sys.modules["opentelemetry"] = original


# ── 2. Span creation ───────────────────────────────────────────


class TestWorkflowTracerSpans:
    """WorkflowTracer — span lifecycle."""

    @pytest.mark.asyncio
    async def test_workflow_run_creates_root_span(self) -> None:
        """run_workflow 时创建 root span "workflow.run"."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer(endpoint="")

        wf = Workflow(
            id="wf-trace-1",
            name="Trace 1",
            steps=[WorkflowStep(id="s1", name="S1", agent="a", prompt="p")],
        )
        executor = FunctionExecutor(lambda s, c: f"out-{s.id}")
        # attach tracer to orchestrator
        result = await run_workflow(wf, executor, tracer=tracer)

        assert result.status == WorkflowStatus.COMPLETED
        # 应该有一个 root span
        spans = tracer.get_recorded_spans()
        assert len(spans) >= 1
        root = spans[0]
        assert root["name"] == "workflow.run"
        assert root["attributes"]["workflow.id"] == "wf-trace-1"

    @pytest.mark.asyncio
    async def test_each_step_creates_child_span(self) -> None:
        """每个 step 创建一个 child span "workflow.step.<id>"."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer(endpoint="")

        wf = Workflow(
            id="wf-trace-2",
            name="Trace 2",
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
        result = await run_workflow(wf, executor, tracer=tracer)

        assert result.status == WorkflowStatus.COMPLETED
        spans = tracer.get_recorded_spans()
        # 1 root + 2 step spans
        step_spans = [
            s for s in spans if s["name"].startswith("workflow.step.")
        ]
        assert len(step_spans) == 2
        ids = {s["attributes"]["step.id"] for s in step_spans}
        assert ids == {"s1", "s2"}

    @pytest.mark.asyncio
    async def test_step_span_has_attributes(self) -> None:
        """step span 包含 attributes: step.id, agent, status, duration."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer(endpoint="")

        wf = Workflow(
            id="wf-attr",
            name="Attr",
            steps=[
                WorkflowStep(id="s1", name="S1", agent="coder", prompt="p")
            ],
        )
        executor = FunctionExecutor(lambda s, c: "ok")
        await run_workflow(wf, executor, tracer=tracer)

        spans = tracer.get_recorded_spans()
        step_span = next(s for s in spans if s["name"] == "workflow.step.s1")
        assert step_span["attributes"]["step.id"] == "s1"
        assert step_span["attributes"]["step.agent"] == "coder"
        assert step_span["attributes"]["step.status"] == "COMPLETED"
        assert "step.duration_ms" in step_span["attributes"]
        assert step_span["attributes"]["step.duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_step_span_has_events(self) -> None:
        """step span 包含 events: started, completed."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer(endpoint="")

        wf = Workflow(
            id="wf-events",
            name="Events",
            steps=[WorkflowStep(id="s1", name="S1", agent="a", prompt="p")],
        )
        executor = FunctionExecutor(lambda s, c: "ok")
        await run_workflow(wf, executor, tracer=tracer)

        spans = tracer.get_recorded_spans()
        step_span = next(s for s in spans if s["name"] == "workflow.step.s1")
        event_names = {e["name"] for e in step_span["events"]}
        assert "step.started" in event_names
        assert "step.completed" in event_names

    @pytest.mark.asyncio
    async def test_failed_step_span_has_failed_event(self) -> None:
        """失败的 step span 有 step.failed event + status=FAILED."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer(endpoint="")

        def failing_step(step, ctx):
            raise RuntimeError("boom")

        wf = Workflow(
            id="wf-fail",
            name="Fail",
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
        executor = FunctionExecutor(failing_step)
        await run_workflow(wf, executor, tracer=tracer)

        spans = tracer.get_recorded_spans()
        step_span = next(s for s in spans if s["name"] == "workflow.step.s1")
        assert step_span["attributes"]["step.status"] == "FAILED"
        event_names = {e["name"] for e in step_span["events"]}
        assert "step.failed" in event_names

    @pytest.mark.asyncio
    async def test_root_span_has_workflow_status(self) -> None:
        """root span 的 attributes 包含 workflow.status."""
        from xruntime._runtime._workflow._tracer import WorkflowTracer

        tracer = WorkflowTracer(endpoint="")

        wf = Workflow(
            id="wf-root",
            name="Root",
            steps=[WorkflowStep(id="s1", name="S1", agent="a", prompt="p")],
        )
        executor = FunctionExecutor(lambda s, c: "ok")
        await run_workflow(wf, executor, tracer=tracer)

        spans = tracer.get_recorded_spans()
        root = spans[0]
        assert root["attributes"]["workflow.status"] == "COMPLETED"
        assert root["attributes"]["workflow.id"] == "wf-root"
        assert root["attributes"]["workflow.name"] == "Root"


# ── 3. No-op tracer (OTel unavailable) ────────────────────────


class TestWorkflowTracerNoOp:
    """WorkflowTracer — no-op mode when OTel is unavailable."""

    @pytest.mark.asyncio
    async def test_noop_tracer_does_not_record_spans(self) -> None:
        """no-op tracer 不记录 spans(返回空列表)."""
        from xruntime._runtime._workflow._tracer import (
            NoOpWorkflowTracer,
        )

        tracer = NoOpWorkflowTracer()
        wf = Workflow(
            id="wf-noop",
            name="NoOp",
            steps=[WorkflowStep(id="s1", name="S1", agent="a", prompt="p")],
        )
        executor = FunctionExecutor(lambda s, c: "ok")
        result = await run_workflow(wf, executor, tracer=tracer)

        assert result.status == WorkflowStatus.COMPLETED
        # no-op tracer 不记录
        assert tracer.get_recorded_spans() == []

    @pytest.mark.asyncio
    async def test_none_tracer_works_as_noop(self) -> None:
        """tracer=None 时 workflow 仍正常执行(无追踪)."""
        wf = Workflow(
            id="wf-none",
            name="None",
            steps=[WorkflowStep(id="s1", name="S1", agent="a", prompt="p")],
        )
        executor = FunctionExecutor(lambda s, c: "ok")
        # tracer=None 不应抛异常
        result = await run_workflow(wf, executor, tracer=None)
        assert result.status == WorkflowStatus.COMPLETED
