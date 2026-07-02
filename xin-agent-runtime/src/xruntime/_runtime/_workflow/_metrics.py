# -*- coding: utf-8 -*-
"""P3-D: WorkflowMetrics — per-step metrics collection.

Tracks workflow execution metrics for Prometheus export:

* ``workflow_step_total{workflow,step,status}`` — counter
* ``workflow_step_duration_ms{workflow,step}`` — histogram (list)
* ``workflow_checkpoint_save_total{workflow}`` — counter
* ``workflow_resume_total{workflow}`` — counter

All metrics are in-memory (matching the existing
:class:`MetricsCollector` pattern).  :meth:`export_prometheus`
produces Prometheus text format for scraping via ``/metrics``.
"""
from __future__ import annotations

from collections import defaultdict


class WorkflowMetrics:
    """In-memory per-step metrics collector for workflows.

    Tracks step counts, durations, checkpoint saves, and resume
    events.  All data is in-memory; use :meth:`export_prometheus`
    for scraping.
    """

    def __init__(self) -> None:
        """Initialize the collector."""
        # {(workflow_id, step_id, status): count}
        self._step_counts: dict[tuple[str, str, str], int] = defaultdict(int)
        # {(workflow_id, step_id): [duration_ms, ...]}
        self._step_durations: dict[tuple[str, str], list[float]] = defaultdict(
            list
        )
        # {workflow_id: count}
        self._checkpoint_saves: dict[str, int] = defaultdict(int)
        # {workflow_id: count}
        self._resumes: dict[str, int] = defaultdict(int)
        # {credential_id: expiry_timestamp}
        self._credential_expiry: dict[str, float] = {}
        # {total_failures: int}
        self._auto_rotation_failures: int = 0
        # {total_timeouts: int}
        self._approval_timeouts: int = 0
        # {approver: pending_count}
        self._approval_pending: dict[str, int] = defaultdict(int)
        # {active: int, max: int}
        self._redis_pool: dict[str, int] = {"active": 0, "max": 0}

    # ── recording ───────────────────────────────────────────────

    def record_step(
        self,
        workflow_id: str,
        step_id: str,
        status: str,
    ) -> None:
        """Record a step execution.

        Args:
            workflow_id (`str`): The workflow id.
            step_id (`str`): The step id.
            status (`str`): Step status (``"COMPLETED"`` / ``"FAILED"``
                / ``"SKIPPED"``).
        """
        self._step_counts[(workflow_id, step_id, status)] += 1

    def record_step_duration(
        self,
        workflow_id: str,
        step_id: str,
        duration_ms: float,
    ) -> None:
        """Record a step's execution duration.

        Args:
            workflow_id (`str`): The workflow id.
            step_id (`str`): The step id.
            duration_ms (`float`): Duration in milliseconds.
        """
        self._step_durations[(workflow_id, step_id)].append(duration_ms)

    def record_checkpoint_save(self, workflow_id: str) -> None:
        """Record a checkpoint save event.

        Args:
            workflow_id (`str`): The workflow id.
        """
        self._checkpoint_saves[workflow_id] += 1

    def record_resume(self, workflow_id: str) -> None:
        """Record a workflow resume event.

        Args:
            workflow_id (`str`): The workflow id.
        """
        self._resumes[workflow_id] += 1

    def record_credential_expiry(
        self,
        credential_id: str,
        expiry_timestamp: float,
    ) -> None:
        """Record a credential's expiry timestamp (gauge).

        Args:
            credential_id (`str`): The credential id.
            expiry_timestamp (`float`): Unix epoch expiry time.
        """
        self._credential_expiry[credential_id] = expiry_timestamp

    def record_auto_rotation_failure(self) -> None:
        """Record an auto-rotation failure (counter)."""
        self._auto_rotation_failures += 1

    def record_approval_timeout(self) -> None:
        """Record an approval timeout (counter)."""
        self._approval_timeouts += 1

    def set_approval_pending(self, approver: str, count: int) -> None:
        """Set the pending approval count for an approver (gauge).

        Args:
            approver (`str`): The approver identifier.
            count (`int`): The pending count.
        """
        self._approval_pending[approver] = count

    def set_redis_pool_stats(
        self,
        active: int,
        max_connections: int,
    ) -> None:
        """Set Redis connection pool stats (gauge).

        Args:
            active (`int`): Active connections.
            max_connections (`int`): Max pool size.
        """
        self._redis_pool["active"] = active
        self._redis_pool["max"] = max_connections

    # ── queries ─────────────────────────────────────────────────

    def get_step_count(
        self,
        workflow_id: str,
        step_id: str,
        status: str,
    ) -> int:
        """Return the count for a specific step status.

        Args:
            workflow_id (`str`): The workflow id.
            step_id (`str`): The step id.
            status (`str`): The status to query.

        Returns:
            `int`: The count (0 if none recorded).
        """
        return self._step_counts.get(
            (workflow_id, step_id, status),
            0,
        )

    def get_step_durations(
        self,
        workflow_id: str,
        step_id: str,
    ) -> list[float]:
        """Return all recorded durations for a step.

        Args:
            workflow_id (`str`): The workflow id.
            step_id (`str`): The step id.

        Returns:
            `list[float]`: Durations in ms (empty if none).
        """
        return list(self._step_durations.get((workflow_id, step_id), []))

    def get_checkpoint_save_count(self, workflow_id: str) -> int:
        """Return checkpoint save count for a workflow."""
        return self._checkpoint_saves.get(workflow_id, 0)

    def get_resume_count(self, workflow_id: str) -> int:
        """Return resume count for a workflow."""
        return self._resumes.get(workflow_id, 0)

    # ── Prometheus export ──────────────────────────────────────

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus text format.

        Returns:
            `str`: Prometheus text format metrics.
        """
        lines: list[str] = []

        # workflow_step_total
        if self._step_counts:
            lines.append(
                "# HELP workflow_step_total Total workflow steps by " "status",
            )
            lines.append("# TYPE workflow_step_total counter")
            for (wf, step, status), count in sorted(
                self._step_counts.items(),
            ):
                lines.append(
                    f'workflow_step_total{{workflow="{wf}",'
                    f'step="{step}",status="{status}"}} {count}',
                )

        # workflow_step_duration_ms
        if self._step_durations:
            lines.append(
                "# HELP workflow_step_duration_ms Step execution "
                "duration in ms",
            )
            lines.append(
                "# TYPE workflow_step_duration_ms histogram",
            )
            for (wf, step), durations in sorted(
                self._step_durations.items(),
            ):
                if durations:
                    avg = sum(durations) / len(durations)
                    lines.append(
                        f'workflow_step_duration_ms_avg{{workflow="{wf}",'
                        f'step="{step}"}} {round(avg, 2)}',
                    )
                    lines.append(
                        f'workflow_step_duration_ms_count{{workflow="{wf}",'
                        f'step="{step}"}} {len(durations)}',
                    )

        # workflow_checkpoint_save_total
        if self._checkpoint_saves:
            lines.append(
                "# HELP workflow_checkpoint_save_total Total "
                "checkpoint saves per workflow",
            )
            lines.append(
                "# TYPE workflow_checkpoint_save_total counter",
            )
            for wf, count in sorted(self._checkpoint_saves.items()):
                lines.append(
                    f'workflow_checkpoint_save_total{{workflow="{wf}"}} '
                    f"{count}",
                )

        # workflow_resume_total
        if self._resumes:
            lines.append(
                "# HELP workflow_resume_total Total workflow resumes",
            )
            lines.append("# TYPE workflow_resume_total counter")
            for wf, count in sorted(self._resumes.items()):
                lines.append(
                    f'workflow_resume_total{{workflow="{wf}"}} {count}',
                )

        # xruntime_credential_expiry_seconds (gauge)
        if self._credential_expiry:
            lines.append(
                "# HELP xruntime_credential_expiry_seconds "
                "Seconds until credential expiry",
            )
            lines.append(
                "# TYPE xruntime_credential_expiry_seconds gauge",
            )
            for cred_id, exp in sorted(
                self._credential_expiry.items(),
            ):
                lines.append(
                    f"xruntime_credential_expiry_seconds"
                    f'{{credential_id="{cred_id}"}} {round(exp, 0)}',
                )

        # xruntime_auto_rotation_failures_total (counter)
        if self._auto_rotation_failures > 0:
            lines.append(
                "# HELP xruntime_auto_rotation_failures_total "
                "Total credential auto-rotation failures",
            )
            lines.append(
                "# TYPE xruntime_auto_rotation_failures_total " "counter",
            )
            lines.append(
                f"xruntime_auto_rotation_failures_total "
                f"{self._auto_rotation_failures}",
            )

        # xruntime_approval_timed_out_total (counter)
        if self._approval_timeouts > 0:
            lines.append(
                "# HELP xruntime_approval_timed_out_total "
                "Total approval timeouts",
            )
            lines.append(
                "# TYPE xruntime_approval_timed_out_total counter",
            )
            lines.append(
                f"xruntime_approval_timed_out_total "
                f"{self._approval_timeouts}",
            )

        # xruntime_approval_pending_count (gauge)
        if self._approval_pending:
            lines.append(
                "# HELP xruntime_approval_pending_count "
                "Pending approval requests per approver",
            )
            lines.append(
                "# TYPE xruntime_approval_pending_count gauge",
            )
            for approver, count in sorted(
                self._approval_pending.items(),
            ):
                lines.append(
                    f"xruntime_approval_pending_count"
                    f'{{approver="{approver}"}} {count}',
                )

        # xruntime_redis_pool metrics (gauge)
        if self._redis_pool["max"] > 0:
            lines.append(
                "# HELP xruntime_redis_pool_active_connections "
                "Active Redis connections",
            )
            lines.append(
                "# TYPE xruntime_redis_pool_active_connections " "gauge",
            )
            lines.append(
                f"xruntime_redis_pool_active_connections "
                f"{self._redis_pool['active']}",
            )
            lines.append(
                "# HELP xruntime_redis_pool_max_connections "
                "Max Redis pool size",
            )
            lines.append(
                "# TYPE xruntime_redis_pool_max_connections gauge",
            )
            lines.append(
                f"xruntime_redis_pool_max_connections "
                f"{self._redis_pool['max']}",
            )

        return "\n".join(lines) + "\n" if lines else ""


__all__ = ["WorkflowMetrics"]
