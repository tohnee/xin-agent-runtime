# -*- coding: utf-8 -*-
"""TDD tests for BenchmarkSuite (P4-D).

Covers:

* BenchmarkResult — statistics (avg/p50/p95/p99/throughput).
* BenchmarkSuite — run / run_all_scenarios / compare.
* Built-in scenario builders (linear/parallel/large/conditional/loop).
* Regression detection via compare().
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from xruntime._runtime._workflow._sdk import FunctionExecutor


# ── helpers ──────────────────────────────────────────────────────


def _noop_step(step: WorkflowStep, ctx: dict[str, Any]) -> str:
    """No-op step that returns immediately."""
    return f"out-{step.id}"


def _slow_step(delay_ms: float = 10.0):
    """Step that sleeps for a configurable delay."""

    def fn(step: WorkflowStep, ctx: dict[str, Any]) -> str:
        time.sleep(delay_ms / 1000.0)
        return f"out-{step.id}"

    return fn


# ── 1. BenchmarkResult statistics ─────────────────────────────


class TestBenchmarkResult:
    """BenchmarkResult — statistics computation."""

    def test_empty_result_returns_zeros(self) -> None:
        """空 result 返回 0."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        r = BenchmarkResult()
        assert r.avg_ms == 0.0
        assert r.p50_ms == 0.0
        assert r.p95_ms == 0.0
        assert r.p99_ms == 0.0
        assert r.throughput == 0.0
        assert r.error_count == 0
        assert r.success_rate == 0.0

    def test_avg_ms(self) -> None:
        """avg_ms 计算平均值."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        r = BenchmarkResult(latencies_ms=[10.0, 20.0, 30.0])
        assert r.avg_ms == pytest.approx(20.0)

    def test_p50_ms(self) -> None:
        """p50_ms 计算中位数."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        r = BenchmarkResult(latencies_ms=[10.0, 20.0, 30.0, 40.0, 50.0])
        assert r.p50_ms == pytest.approx(30.0)

    def test_p95_ms(self) -> None:
        """p95_ms 计算 95 分位."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        latencies = [float(i) for i in range(1, 101)]  # 1..100
        r = BenchmarkResult(latencies_ms=latencies)
        assert r.p95_ms >= 90.0
        assert r.p95_ms <= 100.0

    def test_p99_ms(self) -> None:
        """p99_ms 计算 99 分位."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        latencies = [float(i) for i in range(1, 101)]
        r = BenchmarkResult(latencies_ms=latencies)
        assert r.p99_ms >= 95.0
        assert r.p99_ms <= 100.0

    def test_throughput(self) -> None:
        """throughput = iterations / total_seconds."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        r = BenchmarkResult(
            iterations=100,
            total_ms=1000.0,
            latencies_ms=[10.0] * 100,
        )
        assert r.throughput == pytest.approx(100.0)  # 100 wf/s

    def test_success_rate_no_errors(self) -> None:
        """无错误时 success_rate=1.0."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        r = BenchmarkResult(iterations=10, latencies_ms=[10.0] * 10)
        assert r.success_rate == 1.0

    def test_success_rate_with_errors(self) -> None:
        """有错误时 success_rate < 1.0."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        r = BenchmarkResult(
            iterations=10,
            errors=["err1", "err2"],
            latencies_ms=[10.0] * 8,
        )
        assert r.success_rate == pytest.approx(0.8)

    def test_summary_contains_stats(self) -> None:
        """summary() 包含所有关键指标."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        r = BenchmarkResult(
            scenario="linear_10",
            iterations=100,
            latencies_ms=[10.0] * 100,
            total_ms=1000.0,
        )
        s = r.summary()
        assert "linear_10" in s
        assert "Avg" in s
        assert "P50" in s
        assert "P95" in s
        assert "Throughput" in s

    def test_p95_ms_single_element(self) -> None:
        """p95_ms 单元素列表返回该元素."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        r = BenchmarkResult(latencies_ms=[42.0])
        assert r.p95_ms == 42.0

    def test_p99_ms_single_element(self) -> None:
        """p99_ms 单元素列表返回该元素."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        r = BenchmarkResult(latencies_ms=[42.0])
        assert r.p99_ms == 42.0

    def test_to_dict(self) -> None:
        """to_dict 返回可 JSON 序列化的 dict."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
        )

        r = BenchmarkResult(
            scenario="test",
            iterations=10,
            latencies_ms=[10.0] * 10,
            total_ms=100.0,
        )
        d = r.to_dict()
        assert d["scenario"] == "test"
        assert d["iterations"] == 10
        assert "avg_ms" in d
        assert "p95_ms" in d
        assert "throughput" in d


# ── 2. BenchmarkSuite run ─────────────────────────────────────


class TestBenchmarkSuiteRun:
    """BenchmarkSuite — run method."""

    @pytest.mark.asyncio
    async def test_run_linear_workflow(self) -> None:
        """运行线性 workflow 基准."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        suite = BenchmarkSuite()
        wf = BenchmarkSuite.build_linear_workflow(5)
        executor = FunctionExecutor(_noop_step)
        result = await suite.run(
            wf,
            executor,
            iterations=10,
            scenario="linear_5",
        )
        assert result.scenario == "linear_5"
        assert result.iterations == 10
        assert len(result.latencies_ms) == 10
        assert result.error_count == 0
        assert result.success_rate == 1.0
        assert result.avg_ms > 0

    @pytest.mark.asyncio
    async def test_run_parallel_workflow(self) -> None:
        """运行并行 workflow 基准."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        suite = BenchmarkSuite()
        wf = BenchmarkSuite.build_parallel_workflow(5)
        executor = FunctionExecutor(_noop_step)
        result = await suite.run(
            wf,
            executor,
            iterations=10,
            scenario="parallel_5",
        )
        assert result.iterations == 10
        assert result.success_rate == 1.0

    @pytest.mark.asyncio
    async def test_run_with_concurrency(self) -> None:
        """并发运行 workflow(concurrency=4)."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        suite = BenchmarkSuite()
        wf = BenchmarkSuite.build_linear_workflow(3)
        executor = FunctionExecutor(_noop_step)
        result = await suite.run(
            wf,
            executor,
            iterations=20,
            concurrency=4,
            scenario="concurrent",
        )
        assert result.concurrency == 4
        assert len(result.latencies_ms) == 20

    @pytest.mark.asyncio
    async def test_run_with_warmup(self) -> None:
        """warmup iterations 不计入结果."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        suite = BenchmarkSuite(warmup_iterations=3)
        wf = BenchmarkSuite.build_linear_workflow(2)
        executor = FunctionExecutor(_noop_step)
        result = await suite.run(
            wf,
            executor,
            iterations=5,
        )
        # warmup 不计入 iterations
        assert result.iterations == 5

    @pytest.mark.asyncio
    async def test_run_records_errors(self) -> None:
        """executor 抛异常时记录错误."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        def failing_step(step, ctx):
            raise RuntimeError("boom")

        suite = BenchmarkSuite()
        wf = BenchmarkSuite.build_linear_workflow(2)
        executor = FunctionExecutor(failing_step)
        result = await suite.run(
            wf,
            executor,
            iterations=5,
            scenario="failing",
        )
        assert result.error_count == 5
        assert result.success_rate == 0.0

    @pytest.mark.asyncio
    async def test_run_with_concurrent_failing_works(self) -> None:
        """并发模式下 executor 异常也被正确捕获."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        call_count = 0

        def flaky_step(step, ctx):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise RuntimeError("flaky")
            return f"out-{step.id}"

        suite = BenchmarkSuite()
        wf = BenchmarkSuite.build_linear_workflow(2)
        executor = FunctionExecutor(flaky_step)
        result = await suite.run(
            wf,
            executor,
            iterations=10,
            concurrency=2,
            scenario="flaky-concurrent",
        )
        # 至少有一些错误
        assert result.error_count > 0
        assert result.success_rate < 1.0

    @pytest.mark.asyncio
    async def test_run_once_catches_orchestrator_exception(self) -> None:
        """_run_once 捕获 orchestrator 级异常并记录."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )
        from unittest.mock import patch, AsyncMock

        suite = BenchmarkSuite()
        wf = BenchmarkSuite.build_linear_workflow(2)
        executor = FunctionExecutor(_noop_step)

        # Mock Orchestrator.run to raise
        async def failing_run(self, workflow):
            raise RuntimeError("orchestrator crash")

        with patch(
            "xruntime._runtime._orchestrator.Orchestrator.run",
            failing_run,
        ):
            result = await suite.run(
                wf,
                executor,
                iterations=3,
                scenario="crash",
            )
        assert result.error_count == 3
        assert result.success_rate == 0.0
        assert len(result.errors) == 3


# ── 3. Built-in scenario builders ─────────────────────────────


class TestBenchmarkScenarioBuilders:
    """BenchmarkSuite — built-in scenario builders."""

    def test_build_linear_workflow(self) -> None:
        """线性 workflow 构建."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        wf = BenchmarkSuite.build_linear_workflow(10)
        assert len(wf.steps) == 10
        assert wf.steps[0].depends_on == []
        assert wf.steps[1].depends_on == ["s1"]
        assert wf.steps[9].depends_on == ["s9"]

    def test_build_parallel_workflow(self) -> None:
        """并行 workflow 构建."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        wf = BenchmarkSuite.build_parallel_workflow(10)
        assert len(wf.steps) == 10
        # 所有 step 无依赖(并行)
        for step in wf.steps:
            assert step.depends_on == []

    def test_build_large_workflow(self) -> None:
        """大 workflow 构建."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        wf = BenchmarkSuite.build_large_workflow(100)
        assert len(wf.steps) == 100

    def test_build_conditional_workflow(self) -> None:
        """条件 workflow 构建."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        wf = BenchmarkSuite.build_conditional_workflow()
        assert len(wf.steps) == 3
        # 包含 ConditionalStep
        from xruntime._runtime._workflow._steps import ConditionalStep

        assert isinstance(wf.steps[1], ConditionalStep)

    def test_build_loop_workflow(self) -> None:
        """循环 workflow 构建."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        wf = BenchmarkSuite.build_loop_workflow(5)
        assert len(wf.steps) == 1
        from xruntime._runtime._workflow._steps import LoopStep

        assert isinstance(wf.steps[0], LoopStep)
        assert wf.steps[0].max_iterations == 5


# ── 4. run_all_scenarios ──────────────────────────────────────


class TestBenchmarkSuiteRunAll:
    """BenchmarkSuite — run_all_scenarios."""

    @pytest.mark.asyncio
    async def test_run_all_scenarios(self) -> None:
        """运行所有内置场景."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        suite = BenchmarkSuite()
        executor = FunctionExecutor(_noop_step)
        results = await suite.run_all_scenarios(
            executor,
            iterations=5,
        )
        assert len(results) == 5
        names = [r.scenario for r in results]
        assert "linear_10" in names
        assert "parallel_10" in names
        assert "large_100" in names
        assert "conditional" in names
        assert "loop_5" in names

    @pytest.mark.asyncio
    async def test_all_scenarios_complete_successfully(self) -> None:
        """所有场景 100% 成功."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkSuite,
        )

        suite = BenchmarkSuite()
        executor = FunctionExecutor(_noop_step)
        results = await suite.run_all_scenarios(
            executor,
            iterations=3,
        )
        for r in results:
            assert r.success_rate == 1.0
            assert r.error_count == 0


# ── 5. Regression detection ──────────────────────────────────


class TestBenchmarkCompare:
    """BenchmarkSuite.compare — regression detection."""

    def test_compare_no_regression(self) -> None:
        """avg 变化 < threshold → OK."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
            BenchmarkSuite,
        )

        baseline = BenchmarkResult(
            scenario="test",
            latencies_ms=[10.0] * 10,
        )
        candidate = BenchmarkResult(
            scenario="test",
            latencies_ms=[11.0] * 10,
        )
        report = BenchmarkSuite.compare(
            baseline,
            candidate,
            regression_threshold_pct=10.0,
        )
        assert report["regressed"] is False
        assert report["verdict"] == "OK"

    def test_compare_regression_detected(self) -> None:
        """avg 变化 > threshold → REGRESSION."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
            BenchmarkSuite,
        )

        baseline = BenchmarkResult(
            scenario="test",
            latencies_ms=[10.0] * 10,
        )
        candidate = BenchmarkResult(
            scenario="test",
            latencies_ms=[15.0] * 10,  # +50%
        )
        report = BenchmarkSuite.compare(
            baseline,
            candidate,
            regression_threshold_pct=10.0,
        )
        assert report["regressed"] is True
        assert report["verdict"] == "REGRESSION"
        assert report["avg_change_pct"] > 10.0

    def test_compare_baseline_zero(self) -> None:
        """baseline avg=0 时返回 not regressed."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
            BenchmarkSuite,
        )

        baseline = BenchmarkResult(scenario="test", latencies_ms=[])
        candidate = BenchmarkResult(
            scenario="test",
            latencies_ms=[10.0],
        )
        report = BenchmarkSuite.compare(baseline, candidate)
        assert report["regressed"] is False

    def test_compare_improvement_not_regression(self) -> None:
        """性能提升(avg 下降)不算回归."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
            BenchmarkSuite,
        )

        baseline = BenchmarkResult(
            scenario="test",
            latencies_ms=[20.0] * 10,
        )
        candidate = BenchmarkResult(
            scenario="test",
            latencies_ms=[10.0] * 10,  # -50%
        )
        report = BenchmarkSuite.compare(
            baseline,
            candidate,
            regression_threshold_pct=10.0,
        )
        assert report["regressed"] is False
        assert report["avg_change_pct"] < 0

    def test_compare_report_contains_p95(self) -> None:
        """compare 报告包含 P95 变化."""
        from xruntime._runtime._workflow._benchmark import (
            BenchmarkResult,
            BenchmarkSuite,
        )

        baseline = BenchmarkResult(
            scenario="test",
            latencies_ms=[float(i) for i in range(1, 101)],
        )
        candidate = BenchmarkResult(
            scenario="test",
            latencies_ms=[float(i * 2) for i in range(1, 101)],
        )
        report = BenchmarkSuite.compare(baseline, candidate)
        assert "baseline_p95_ms" in report
        assert "candidate_p95_ms" in report
        assert "p95_change_pct" in report
