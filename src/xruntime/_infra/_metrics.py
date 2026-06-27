# -*- coding: utf-8 -*-
"""Prometheus-compatible metrics collector."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


class MetricsCollector:
    """Collects runtime metrics for Prometheus export.

    Tracks:
        - Active sessions per tenant
        - Tool call count and latency
        - Token consumption per tenant

    All metrics are in-memory; for production, export via
    ``export_prometheus()`` to a Prometheus scraper.
    """

    def __init__(self) -> None:
        """Initialize the collector."""
        self._active_sessions: dict[str, int] = defaultdict(int)
        # Bounded per-tool latency ring buffer so a long-lived process
        # does not accumulate unbounded memory per tool.
        self._tool_calls: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=10000),
        )
        self._tokens: dict[str, dict[str, int]] = defaultdict(
            lambda: {"input": 0, "output": 0},
        )
        self._subagent_calls: dict[str, dict[str, Any]] = {}

    def record_session_start(self, tenant_id: str) -> None:
        """Record a session start.

        Args:
            tenant_id (`str`):
                The tenant id.
        """
        self._active_sessions[tenant_id] += 1

    def record_session_end(self, tenant_id: str) -> None:
        """Record a session end.

        Args:
            tenant_id (`str`):
                The tenant id.
        """
        count = self._active_sessions.get(tenant_id, 0)
        if count > 0:
            new_count = count - 1
            if new_count == 0:
                # Remove the tenant so export_prometheus does not emit
                # phantom zero-count rows for tenants that only ended.
                self._active_sessions.pop(tenant_id, None)
            else:
                self._active_sessions[tenant_id] = new_count

    def active_sessions(self, tenant_id: str) -> int:
        """Get active session count for a tenant.

        Args:
            tenant_id (`str`):
                The tenant id.

        Returns:
            `int`: Active session count.
        """
        return self._active_sessions.get(tenant_id, 0)

    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float,
    ) -> None:
        """Record a tool call with latency.

        Args:
            tool_name (`str`):
                The tool name.
            duration_ms (`float`):
                Execution duration in milliseconds.
        """
        self._tool_calls[tool_name].append(duration_ms)

    def tool_call_stats(self, tool_name: str) -> dict[str, Any]:
        """Get tool call statistics.

        Args:
            tool_name (`str`):
                The tool name.

        Returns:
            `dict`: ``{"count": int, "avg_ms": float}``
        """
        durations = self._tool_calls.get(tool_name, [])
        if not durations:
            return {"count": 0, "avg_ms": 0.0}
        return {
            "count": len(durations),
            "avg_ms": sum(durations) / len(durations),
        }

    def record_tokens(
        self,
        tenant_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record token consumption for a tenant.

        Args:
            tenant_id (`str`):
                The tenant id.
            input_tokens (`int`):
                Input tokens consumed.
            output_tokens (`int`):
                Output tokens consumed.
        """
        self._tokens[tenant_id]["input"] += input_tokens
        self._tokens[tenant_id]["output"] += output_tokens

    def token_totals(self, tenant_id: str) -> dict[str, int]:
        """Get token totals for a tenant.

        Args:
            tenant_id (`str`):
                The tenant id.

        Returns:
            `dict`: ``{"input": int, "output": int}``
        """
        return dict(self._tokens.get(tenant_id, {"input": 0, "output": 0}))

    def record_subagent_call(
        self,
        spec_name: str,
        duration_seconds: float,
        success: bool,
        token_usage: int = 0,
    ) -> None:
        """Record a sub-agent execution.

        Args:
            spec_name (`str`):
                The sub-agent spec name.
            duration_seconds (`float`):
                Execution duration in seconds.
            success (`bool`):
                Whether the execution succeeded.
            token_usage (`int`):
                Tokens consumed by the sub-agent.
        """
        key = spec_name
        if key not in self._subagent_calls:
            self._subagent_calls[key] = {
                "count": 0,
                "successes": 0,
                "failures": 0,
                "total_duration": 0.0,
                "total_tokens": 0,
            }
        stats = self._subagent_calls[key]
        stats["count"] += 1
        if success:
            stats["successes"] += 1
        else:
            stats["failures"] += 1
        stats["total_duration"] += duration_seconds
        stats["total_tokens"] += token_usage

    def subagent_stats(self, spec_name: str) -> dict[str, Any]:
        """Get sub-agent execution statistics.

        Args:
            spec_name (`str`): The sub-agent spec name.

        Returns:
            `dict`: Stats with count, successes, failures,
                avg_duration, total_tokens.
        """
        stats = self._subagent_calls.get(
            spec_name,
            {
                "count": 0,
                "successes": 0,
                "failures": 0,
                "total_duration": 0.0,
                "total_tokens": 0,
            },
        )
        avg = (
            stats["total_duration"] / stats["count"]
            if stats["count"] > 0
            else 0.0
        )
        return {
            "count": stats["count"],
            "successes": stats["successes"],
            "failures": stats["failures"],
            "avg_duration_seconds": avg,
            "total_tokens": stats["total_tokens"],
        }

    def export_prometheus(self) -> str:
        """Export all metrics in Prometheus text format.

        Returns:
            `str`: Prometheus exposition text.
        """
        lines: list[str] = []

        # Active sessions
        lines.append(
            "# HELP xruntime_active_sessions Active sessions per tenant"
        )
        lines.append("# TYPE xruntime_active_sessions gauge")
        for tid, count in self._active_sessions.items():
            lines.append(
                f'xruntime_active_sessions{{tenant="{tid}"}} {count}',
            )

        # Tool calls
        lines.append("# HELP xruntime_tool_calls_total Total tool calls")
        lines.append("# TYPE xruntime_tool_calls_total counter")
        for tool, durations in self._tool_calls.items():
            lines.append(
                f'xruntime_tool_calls_total{{tool="{tool}"}} {len(durations)}',
            )

        # Token consumption
        lines.append("# HELP xruntime_tokens_total Total tokens consumed")
        lines.append("# TYPE xruntime_tokens_total counter")
        for tid, totals in self._tokens.items():
            lines.append(
                f'xruntime_tokens_total{{tenant="{tid}",type="input"}} '
                f'{totals["input"]}',
            )
            lines.append(
                f'xruntime_tokens_total{{tenant="{tid}",type="output"}} '
                f'{totals["output"]}',
            )

        # Sub-agent executions
        lines.append(
            "# HELP xruntime_subagent_calls_total "
            "Total sub-agent executions"
        )
        lines.append("# TYPE xruntime_subagent_calls_total counter")
        for spec, stats in self._subagent_calls.items():
            lines.append(
                f"xruntime_subagent_calls_total"
                f'{{spec="{spec}",status="success"}} '
                f'{stats["successes"]}',
            )
            lines.append(
                f"xruntime_subagent_calls_total"
                f'{{spec="{spec}",status="failure"}} '
                f'{stats["failures"]}',
            )

        lines.append(
            "# HELP xruntime_subagent_duration_seconds "
            "Sub-agent execution duration"
        )
        lines.append("# TYPE xruntime_subagent_duration_seconds summary")
        for spec, stats in self._subagent_calls.items():
            avg = (
                stats["total_duration"] / stats["count"]
                if stats["count"] > 0
                else 0.0
            )
            lines.append(
                f"xruntime_subagent_duration_seconds"
                f'{{spec="{spec}"}} {avg:.4f}',
            )

        lines.append(
            "# HELP xruntime_subagent_tokens_total "
            "Total tokens consumed by sub-agents"
        )
        lines.append("# TYPE xruntime_subagent_tokens_total counter")
        for spec, stats in self._subagent_calls.items():
            lines.append(
                f"xruntime_subagent_tokens_total"
                f'{{spec="{spec}"}} {stats["total_tokens"]}',
            )

        return "\n".join(lines) + "\n"
