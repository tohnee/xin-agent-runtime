#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI benchmark runner for XRuntime workflows.

Runs built-in benchmark scenarios and prints results.  Can be used
in CI for regression detection by comparing against a baseline JSON.

Usage::

    # Run all scenarios
    python -m scripts.benchmark --all --iterations 100

    # Run specific scenario
    python -m scripts.benchmark --scenario linear_10 --iterations 50

    # Compare with baseline (CI mode)
    python -m scripts.benchmark --all --compare baseline.json

    # Save results to JSON
    python -m scripts.benchmark --all --output results.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from xruntime._runtime._orchestrator import WorkflowStep
from xruntime._runtime._workflow._benchmark import (
    BenchmarkResult,
    BenchmarkSuite,
)
from xruntime._runtime._workflow._sdk import FunctionExecutor


def _noop_step(step: WorkflowStep, ctx: dict[str, Any]) -> str:
    """No-op step function for benchmarking."""
    return f"out-{step.id}"


async def run_benchmarks(
    iterations: int = 100,
    scenario: str | None = None,
) -> list[BenchmarkResult]:
    """Run benchmark scenarios.

    Args:
        iterations: Number of iterations per scenario.
        scenario: Specific scenario name.  None runs all.

    Returns:
        List of benchmark results.
    """
    suite = BenchmarkSuite(warmup_iterations=3)
    executor = FunctionExecutor(_noop_step)

    if scenario is not None:
        # Run single scenario
        builders = {
            "linear_10": lambda: BenchmarkSuite.build_linear_workflow(10),
            "parallel_10": lambda: BenchmarkSuite.build_parallel_workflow(10),
            "large_100": lambda: BenchmarkSuite.build_large_workflow(100),
            "conditional": BenchmarkSuite.build_conditional_workflow,
            "loop_5": lambda: BenchmarkSuite.build_loop_workflow(5),
        }
        if scenario not in builders:
            print(f"Unknown scenario: {scenario}")
            print(f"Available: {list(builders.keys())}")
            sys.exit(1)
        wf = builders[scenario]()
        result = await suite.run(
            wf, executor, iterations=iterations, scenario=scenario,
        )
        return [result]

    # Run all scenarios
    return await suite.run_all_scenarios(executor, iterations=iterations)


def print_results(results: list[BenchmarkResult]) -> None:
    """Print benchmark results to stdout."""
    print("\n" + "=" * 60)
    print("XRuntime Workflow Benchmark Results")
    print("=" * 60)

    for r in results:
        print()
        print(r.summary())

    print("\n" + "=" * 60)
    print("Summary Table")
    print("-" * 60)
    print(f"{'Scenario':<20} {'Avg':>8} {'P50':>8} {'P95':>8} {'P99':>8} {'TPS':>8}")
    print("-" * 60)
    for r in results:
        print(
            f"{r.scenario:<20} "
            f"{r.avg_ms:>7.2f}ms "
            f"{r.p50_ms:>7.2f}ms "
            f"{r.p95_ms:>7.2f}ms "
            f"{r.p99_ms:>7.2f}ms "
            f"{r.throughput:>7.1f}"
        )
    print("=" * 60)


def save_results(
    results: list[BenchmarkResult], path: str,
) -> None:
    """Save results to a JSON file."""
    data = [r.to_dict() for r in results]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"\nResults saved to {path}")


def compare_with_baseline(
    results: list[BenchmarkResult],
    baseline_path: str,
    threshold: float = 10.0,
) -> bool:
    """Compare results with a baseline JSON file.

    Args:
        results: Current benchmark results.
        baseline_path: Path to baseline JSON.
        threshold: Regression threshold percentage.

    Returns:
        True if no regression, False if regression detected.
    """
    try:
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline_data = json.load(f)
    except FileNotFoundError:
        print(f"Baseline file not found: {baseline_path}")
        print("Skipping comparison (no baseline).")
        return True

    baseline_map = {d["scenario"]: d for d in baseline_data}
    all_ok = True

    print(f"\nComparing with baseline: {baseline_path}")
    print("-" * 60)
    print(
        f"{'Scenario':<20} {'Base Avg':>10} {'Cand Avg':>10} "
        f"{'Change':>8} {'Verdict':>10}"
    )
    print("-" * 60)

    for r in results:
        base = baseline_map.get(r.scenario)
        if base is None:
            print(f"{r.scenario:<20} (no baseline)")
            continue

        base_result = BenchmarkResult(
            scenario=r.scenario,
            latencies_ms=[base["avg_ms"]] * max(base.get("iterations", 1), 1),
        )
        report = BenchmarkSuite.compare(
            base_result, r, regression_threshold_pct=threshold,
        )
        verdict = report["verdict"]
        if verdict == "REGRESSION":
            all_ok = False

        print(
            f"{r.scenario:<20} "
            f"{base['avg_ms']:>9.2f}ms "
            f"{r.avg_ms:>9.2f}ms "
            f"{report['avg_change_pct']:>+7.1f}% "
            f"{verdict:>10}"
        )

    print("-" * 60)
    if all_ok:
        print("✅ No regressions detected.")
    else:
        print("❌ Performance regression detected!")
    return all_ok


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="XRuntime Workflow Benchmark Runner",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Run all built-in scenarios",
    )
    parser.add_argument(
        "--scenario", type=str, default=None,
        help="Specific scenario to run (linear_10, parallel_10, etc.)",
    )
    parser.add_argument(
        "--iterations", type=int, default=100,
        help="Number of iterations per scenario (default: 100)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Save results to JSON file",
    )
    parser.add_argument(
        "--compare", type=str, default=None,
        help="Compare with baseline JSON file",
    )
    parser.add_argument(
        "--threshold", type=float, default=10.0,
        help="Regression threshold percentage (default: 10.0)",
    )
    args = parser.parse_args()

    if not args.all and args.scenario is None:
        parser.print_help()
        sys.exit(1)

    scenario = args.scenario if args.scenario else None
    results = asyncio.run(run_benchmarks(
        iterations=args.iterations,
        scenario=scenario,
    ))

    print_results(results)

    if args.output:
        save_results(results, args.output)

    if args.compare:
        ok = compare_with_baseline(results, args.compare, args.threshold)
        if not ok:
            sys.exit(1)  # CI: non-zero exit = regression


if __name__ == "__main__":
    main()
