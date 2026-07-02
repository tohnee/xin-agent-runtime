# -*- coding: utf-8 -*-
"""P3-D: WorkflowAuditLogger — audit logging for workflow execution.

Records every step execution with tenant isolation and
compliance-relevant fields.  Supports in-memory (default) and
file-based (JSONL) sinks.

Audit entry schema::

    {
        "timestamp": "2026-07-01T12:00:00Z",
        "action": "workflow.step.executed",
        "tenant_id": "tenant-1",
        "workflow_id": "wf-1",
        "step_id": "s1",
        "agent": "coder",
        "status": "COMPLETED",
        "duration_ms": 123.4,
    }

Security: the logger never records ``api_key`` or secret values —
only safe metadata (workflow_id, step_id, agent, status, duration).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("xruntime.workflow.audit")


class WorkflowAuditLogger:
    """Audit logger for workflow execution.

    Args:
        sink (`str`):
            Sink type — ``"memory"`` (default) or ``"file"``.
        file_path (`str | None`):
            File path for ``sink="file"``.  Writes JSONL (one JSON
            object per line).  Appends if the file exists.
        max_entries (`int`):
            Cap on in-memory entries (memory sink only).  Oldest
            entries are dropped first.
    """

    def __init__(
        self,
        sink: str = "memory",
        file_path: str | None = None,
        max_entries: int = 10000,
    ) -> None:
        """Initialize the logger."""
        self._sink = sink
        self._file_path = file_path
        self._max_entries = max_entries
        self._entries: list[dict[str, Any]] = []
        if sink == "file" and file_path is None:
            raise ValueError("file_path is required when sink='file'")

    def record_step(
        self,
        workflow_id: str,
        step_id: str,
        agent: str,
        status: str,
        duration_ms: float,
        tenant_id: str = "",
    ) -> None:
        """Record a step execution audit entry.

        Args:
            workflow_id (`str`): The workflow id.
            step_id (`str`): The step id.
            agent (`str`): The agent name.
            status (`str`): Step status (``"COMPLETED"`` / ``"FAILED"``).
            duration_ms (`float`): Duration in milliseconds.
            tenant_id (`str`): The tenant id (for isolation).
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "workflow.step.executed",
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "step_id": step_id,
            "agent": agent,
            "status": status,
            "duration_ms": duration_ms,
        }
        self._write(entry)

    def record_workflow_event(
        self,
        action: str,
        workflow_id: str,
        tenant_id: str = "",
        status: str = "",
    ) -> None:
        """Record a workflow-level event (started/completed/failed).

        Args:
            action (`str`): ``"workflow.started"`` /
                ``"workflow.completed"`` / ``"workflow.failed"``.
            workflow_id (`str`): The workflow id.
            tenant_id (`str`): The tenant id.
            status (`str`): Workflow status.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "status": status,
        }
        self._write(entry)

    def get_entries(
        self,
        tenant_id: str | None = None,
        workflow_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return audit entries, optionally filtered.

        Args:
            tenant_id (`str | None`):
                Filter by tenant.  ``None`` returns all.
            workflow_id (`str | None`):
                Filter by workflow.  ``None`` returns all.

        Returns:
            `list[dict]`: Matching audit entries.
        """
        if self._sink != "memory":
            return []
        result = self._entries
        if tenant_id is not None:
            result = [e for e in result if e.get("tenant_id") == tenant_id]
        if workflow_id is not None:
            result = [e for e in result if e.get("workflow_id") == workflow_id]
        return list(result)

    def _write(self, entry: dict[str, Any]) -> None:
        """Write an entry to the configured sink."""
        if self._sink == "memory":
            self._entries.append(entry)
            # Enforce max_entries cap (drop oldest)
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries :]
        elif self._sink == "file" and self._file_path is not None:
            try:
                with open(self._file_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to write audit entry to file %s",
                    self._file_path,
                )
        else:
            # Unknown sink — silently drop (or log debug)
            pass


__all__ = ["WorkflowAuditLogger"]
