# -*- coding: utf-8 -*-
"""P4-D: BenchmarkSuite — performance benchmarking for workflows.

Runs a workflow N times and collects latency / throughput
statistics.  Designed for both:

* **CI regression detection** — compare against a baseline, fail
  if regression > threshold.
* **Local profiling** — identify bottlenecks via per-step breakdown.

Benchmark scenarios (built-in):

* ``linear_10`` — 10-step linear chain (DAG depth = 10).
* ``parallel_10`` — 10-step parallel fan-out (single layer).
* ``nested_3`` — 3-level nested sub-workflows.
* ``large_100`` — 100-step workflow (stress test).
* ``conditional`` — ConditionalStep branching.
* ``loop`` — LoopStep iterative refinement.

Statistics collected per scenario:

* ``avg_ms``, ``p50_ms``, ``p95_ms``, ``p99_ms`` — latency.
* ``throughput`` — workflows/second.
* ``total_ms`` — wall-clock total.

Usage::

    from xruntime._runtime._workflow._benchmark import (
        BenchmarkSuite, BenchmarkResult,
    )

    suite = BenchmarkSuite()
    result = await suite.run(
        workflow=my_workflow,
        executor=my_executor,
        iterations=100,
        concurrency=1,
    )
    print(result.summary())
"""
from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from .._orchestrator import (
    Orchestrator,
    StepExecutor,
    Workflow,
    WorkflowStep,
)


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run.

    Args:
        scenario (`str`): Scenario name (e.g. ``"linear_10"``).
        iterations (`int`): Number of iterations run.
        concurrency (`int`): Concurrency level used.
        latencies_ms (`list[float]`): Per-iteration latency in ms.
        total_ms (`float`): Wall-clock total in ms.
        errors (`list[str]`): Error messages from failed iterations.
    """

    scenario: str = ""
    iterations: int = 0
    concurrency: int = 1
    latencies_ms: list[float] = field(default_factory=list)
    total_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def avg_ms(self) -> float:
        """Return average latency in ms."""
        if not self.latencies_ms:
            return 0.0
        return statistics.mean(self.latencies_ms)

    @property
    def p50_ms(self) -> float:
        """Return P50 (median) latency in ms."""
        if not self.latencies_ms:
            return 0.0
        return statistics.median(self.latencies_ms)

    @property
    def p95_ms(self) -> float:
        """Return P95 latency in ms."""
        if not self.latencies_ms:
            return 0.0
        if len(self.latencies_ms) < 2:
            return self.latencies_ms[0]
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p99_ms(self) -> float:
        """Return P99 latency in ms."""
        if not self.latencies_ms:
            return 0.0
        if len(self.latencies_ms) < 2:
            return self.latencies_ms[0]
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def throughput(self) -> float:
        """Return throughput (workflows/second)."""
        if self.total_ms <= 0:
            return 0.0
        return (self.iterations / self.total_ms) * 1000.0

    @property
    def error_count(self) -> int:
        """Return number of failed iterations."""
        return len(self.errors)

    @property
    def success_rate(self) -> float:
        """Return success rate (0.0 to 1.0)."""
        if self.iterations == 0:
            return 0.0
        return (self.iterations - self.error_count) / self.iterations

    def summary(self) -> str:
        """Return a human-readable summary string."""
        return (
            f"Benchmark: {self.scenario}\n"
            f"  Iterations: {self.iterations} (concurrency={self.concurrency})\n"
            f"  Avg:   {self.avg_ms:.2f}ms\n"
            f"  P50:   {self.p50_ms:.2f}ms\n"
            f"  P95:   {self.p95_ms:.2f}ms\n"
            f"  P99:   {self.p99_ms:.2f}ms\n"
            f"  Total: {self.total_ms:.2f}ms\n"
            f"  Throughput: {self.throughput:.2f} wf/s\n"
            f"  Errors: {self.error_count}/{self.iterations}"
            f" ({self.success_rate:.1%} success)"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSON output."""
        return {
            "scenario": self.scenario,
            "iterations": self.iterations,
            "concurrency": self.concurrency,
            "avg_ms": round(self.avg_ms, 2),
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
            "total_ms": round(self.total_ms, 2),
            "throughput": round(self.throughput, 2),
            "error_count": self.error_count,
            "success_rate": round(self.success_rate, 4),
        }


class BenchmarkSuite:
    """Benchmark runner for workflow performance measurement.

    Args:
        warmup_iterations (`int`):
            Number of warmup iterations (not counted in results).
            Defaults to 0.
    """

    def __init__(
        self,
        warmup_iterations: int = 0,
    ) -> None:
        """Initialize the suite."""
        self._warmup = warmup_iterations

    async def run(
        self,
        workflow: Workflow,
        executor: StepExecutor,
        *,
        iterations: int = 100,
        concurrency: int = 1,
        scenario: str = "custom",
    ) -> BenchmarkResult:
        """Run a benchmark on a workflow.

        Args:
            workflow (`Workflow`): The workflow to benchmark.
            executor (`StepExecutor`): The step executor.
            iterations (`int`): Number of iterations to run.
            concurrency (`int`): Number of concurrent workflows.
            scenario (`str`): Scenario name for labeling.

        Returns:
            `BenchmarkResult`: The benchmark result.
        """
        # Warmup (not counted)
        for _ in range(self._warmup):
            orch = Orchestrator(executor=executor)
            await orch.run(workflow)

        result = BenchmarkResult(
            scenario=scenario,
            iterations=iterations,
            concurrency=concurrency,
        )

        start_total = time.perf_counter()

        if concurrency <= 1:
            # Sequential execution
            for _ in range(iterations):
                latency = await self._run_once(
                    workflow,
                    executor,
                    result,
                )
                if latency is not None:
                    result.latencies_ms.append(latency)
        else:
            # Concurrent execution
            sem = asyncio.Semaphore(concurrency)

            async def run_with_sem() -> float | None:
                async with sem:
                    return await self._run_once(
                        workflow,
                        executor,
                        result,
                    )

            tasks = [run_with_sem() for _ in range(iterations)]
            latencies = await asyncio.gather(*tasks)
            for lat in latencies:
                if lat is not None:
                    result.latencies_ms.append(lat)

        result.total_ms = (time.perf_counter() - start_total) * 1000
        return result

    async def _run_once(
        self,
        workflow: Workflow,
        executor: StepExecutor,
        result: BenchmarkResult,
    ) -> float | None:
        """Run a single iteration, returning latency in ms."""
        orch = Orchestrator(executor=executor)
        start = time.perf_counter()
        try:
            wf_result = await orch.run(workflow)
            elapsed_ms = (time.perf_counter() - start) * 1000
            if wf_result.status != "COMPLETED":
                result.errors.append(
                    f"Workflow did not complete: {wf_result.status}",
                )
                return None
            return elapsed_ms
        except Exception as e:  # noqa: BLE001
            result.errors.append(str(e))
            return None

    # ── built-in scenario builders ─────────────────────────────

    @staticmethod
    def build_linear_workflow(
        num_steps: int = 10,
    ) -> Workflow:
        """Build a linear chain workflow (s1→s2→...→sN).

        Args:
            num_steps (`int`): Number of steps in the chain.

        Returns:
            `Workflow`: The linear workflow.
        """
        steps: list[WorkflowStep] = []
        for i in range(num_steps):
            step_id = f"s{i + 1}"
            deps = [f"s{i}"] if i > 0 else []
            steps.append(
                WorkflowStep(
                    id=step_id,
                    name=f"Step {i + 1}",
                    agent="benchmark",
                    prompt="noop",
                    depends_on=deps,
                ),
            )
        return Workflow(
            id=f"bench-linear-{num_steps}",
            name=f"Linear {num_steps}",
            steps=steps,
        )

    @staticmethod
    def build_parallel_workflow(
        num_steps: int = 10,
    ) -> Workflow:
        """Build a parallel fan-out workflow (all steps in one layer).

        Args:
            num_steps (`int`): Number of parallel steps.

        Returns:
            `Workflow`: The parallel workflow.
        """
        steps = [
            WorkflowStep(
                id=f"s{i + 1}",
                name=f"Step {i + 1}",
                agent="benchmark",
                prompt="noop",
            )
            for i in range(num_steps)
        ]
        return Workflow(
            id=f"bench-parallel-{num_steps}",
            name=f"Parallel {num_steps}",
            steps=steps,
        )

    @staticmethod
    def build_large_workflow(
        num_steps: int = 100,
    ) -> Workflow:
        """Build a large workflow with mixed deps.

        Args:
            num_steps (`int`): Total number of steps.

        Returns:
            `Workflow`: The large workflow.
        """
        steps: list[WorkflowStep] = []
        for i in range(num_steps):
            step_id = f"s{i + 1}"
            # Every 5th step depends on the previous 5
            if i > 0 and i % 5 == 0:
                deps = [f"s{j}" for j in range(max(0, i - 4), i + 1)]
            elif i > 0:
                deps = [f"s{i}"]
            else:
                deps = []
            steps.append(
                WorkflowStep(
                    id=step_id,
                    name=f"Step {i + 1}",
                    agent="benchmark",
                    prompt="noop",
                    depends_on=deps,
                ),
            )
        return Workflow(
            id=f"bench-large-{num_steps}",
            name=f"Large {num_steps}",
            steps=steps,
        )

    @staticmethod
    def build_conditional_workflow() -> Workflow:
        """Build a workflow with ConditionalStep branches."""
        from ._steps import ConditionalStep

        return Workflow(
            id="bench-conditional",
            name="Conditional Benchmark",
            steps=[
                WorkflowStep(
                    id="classify",
                    name="Classify",
                    agent="benchmark",
                    prompt="noop",
                ),
                ConditionalStep(
                    id="branch-urgent",
                    name="Urgent Branch",
                    agent="benchmark",
                    prompt="noop",
                    condition=lambda ctx: True,
                    inner_steps=[
                        WorkflowStep(
                            id="escalate",
                            name="Escalate",
                            agent="benchmark",
                            prompt="noop",
                        ),
                    ],
                    depends_on=["classify"],
                ),
                ConditionalStep(
                    id="branch-normal",
                    name="Normal Branch",
                    agent="benchmark",
                    prompt="noop",
                    condition=lambda ctx: False,
                    inner_steps=[
                        WorkflowStep(
                            id="queue",
                            name="Queue",
                            agent="benchmark",
                            prompt="noop",
                        ),
                    ],
                    depends_on=["classify"],
                ),
            ],
        )

    @staticmethod
    def build_loop_workflow(
        max_iterations: int = 5,
    ) -> Workflow:
        """Build a workflow with a LoopStep."""
        from ._steps import LoopStep

        return Workflow(
            id="bench-loop",
            name="Loop Benchmark",
            steps=[
                LoopStep(
                    id="loop",
                    name="Refine Loop",
                    agent="benchmark",
                    prompt="noop",
                    condition=lambda ctx: True,
                    max_iterations=max_iterations,
                ),
            ],
        )

    async def run_all_scenarios(
        self,
        executor: StepExecutor,
        iterations: int = 50,
    ) -> list[BenchmarkResult]:
        """Run all built-in benchmark scenarios.

        Args:
            executor (`StepExecutor`): The step executor.
            iterations (`int`): Iterations per scenario.

        Returns:
            `list[BenchmarkResult]`: Results for each scenario.
        """
        scenarios: list[tuple[str, Workflow]] = [
            ("linear_10", self.build_linear_workflow(10)),
            ("parallel_10", self.build_parallel_workflow(10)),
            ("large_100", self.build_large_workflow(100)),
            ("conditional", self.build_conditional_workflow()),
            ("loop_5", self.build_loop_workflow(5)),
        ]

        results: list[BenchmarkResult] = []
        for name, wf in scenarios:
            result = await self.run(
                wf,
                executor,
                iterations=iterations,
                concurrency=1,
                scenario=name,
            )
            results.append(result)
        return results

    @staticmethod
    def compare(
        baseline: BenchmarkResult,
        candidate: BenchmarkResult,
        regression_threshold_pct: float = 10.0,
    ) -> dict[str, Any]:
        """Compare two benchmark results for regression detection.

        Args:
            baseline (`BenchmarkResult`): The baseline result.
            candidate (`BenchmarkResult`): The candidate result.
            regression_threshold_pct (`float`):
                Threshold percentage for regression alert.

        Returns:
            `dict`: Comparison report with ``regressed`` flag.
        """
        if baseline.avg_ms == 0:
            return {"regressed": False, "reason": "baseline is zero"}

        avg_change_pct = (
            (candidate.avg_ms - baseline.avg_ms) / baseline.avg_ms
        ) * 100
        p95_change_pct = (
            (candidate.p95_ms - baseline.p95_ms) / baseline.p95_ms
            if baseline.p95_ms > 0
            else 0.0
        ) * 100

        regressed = avg_change_pct > regression_threshold_pct

        return {
            "regressed": regressed,
            "scenario": candidate.scenario,
            "baseline_avg_ms": round(baseline.avg_ms, 2),
            "candidate_avg_ms": round(candidate.avg_ms, 2),
            "avg_change_pct": round(avg_change_pct, 2),
            "baseline_p95_ms": round(baseline.p95_ms, 2),
            "candidate_p95_ms": round(candidate.p95_ms, 2),
            "p95_change_pct": round(p95_change_pct, 2),
            "threshold_pct": regression_threshold_pct,
            "verdict": "REGRESSION" if regressed else "OK",
        }


__all__ = ["BenchmarkResult", "BenchmarkSuite"]
